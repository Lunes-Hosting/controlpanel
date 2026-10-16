"""Automatic static versioning/compression and reduced session-storage traffic."""

from functools import lru_cache
import gzip
import hashlib
import mimetypes
from pathlib import Path
import time

from flask import request, session, send_from_directory
from flask.sessions import SessionInterface


@lru_cache(maxsize=512)
def _file_version(filename, size, modified_ns):
    digest = hashlib.blake2s(digest_size=8)
    with open(filename, 'rb') as source:
        for chunk in iter(lambda: source.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=512)
def _gzip_matches(source, version, compressed, size, modified_ns):
    try:
        with gzip.open(compressed, 'rb') as stream:
            data = stream.read(Path(source).stat().st_size + 1)
        return hashlib.blake2s(data, digest_size=8).hexdigest() == version
    except (OSError, EOFError):
        return False


def asset_version(app, filename):
    root = Path(app.static_folder).resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    details = path.stat()
    return _file_version(str(path), details.st_size, details.st_mtime_ns)


class EfficientSessionInterface(SessionInterface):
    """Keep session rotation/deletion while coalescing unchanged expiry refreshes."""
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def open_session(self, app, req):
        if req.path.startswith((app.static_url_path or '/static') + '/'):
            return self.make_null_session(app)
        return self.delegate.open_session(app, req)

    def save_session(self, app, sess, response):
        if getattr(sess, 'accessed', False):
            response.vary.add('Cookie')
        last_refresh = sess.get('_storage_refreshed_at', 0)
        if not isinstance(last_refresh, (int, float)):
            last_refresh = 0
        if not sess.modified and (not sess.get('email') or time.time() - last_refresh < 3600):
            return
        if sess:
            sess['_storage_refreshed_at'] = time.time()
        self.delegate.save_session(app, sess, response)


def install_performance(app):
    app.session_interface = EfficientSessionInterface(app.session_interface)

    @app.url_defaults
    def version_static_url(endpoint, values):
        if endpoint == 'static' and isinstance(values.get('filename'), str):
            version = asset_version(app, values['filename'])
            if version:
                values['v'] = version

    @app.before_request
    def precompressed_static():
        if (request.endpoint != 'static' or request.method not in ('GET', 'HEAD')
                or request.headers.get('Range') or request.accept_encodings.quality('gzip') <= 0):
            return None
        filename = request.view_args['filename']
        if Path(filename).suffix not in ('.css', '.js', '.svg'):
            return None
        root = Path(app.static_folder).resolve()
        source = (root / filename).resolve()
        compressed = source.with_name(source.name + '.gz')
        if (not source.is_relative_to(root) or not source.is_file() or not compressed.is_file()
                or not _gzip_matches(str(source), asset_version(app, filename), str(compressed),
                                        compressed.stat().st_size, compressed.stat().st_mtime_ns)):
            return None
        response = send_from_directory(app.static_folder, filename + '.gz',
                                       mimetype=mimetypes.guess_type(filename)[0], conditional=True)
        response.headers['Content-Encoding'] = 'gzip'
        response.vary.add('Accept-Encoding')
        return response

    @app.after_request
    def optimize_response(response):
        if request.endpoint == 'static':
            response.vary.add('Accept-Encoding')
            filename = (request.view_args or {}).get('filename', '')
            version = asset_version(app, filename) if filename else None
            if version and request.args.get('v') == version:
                response.cache_control.public = True
                response.cache_control.no_cache = False
                response.cache_control.max_age = 31536000
                response.cache_control.immutable = True
            return response
        if response.mimetype not in ('text/html', 'application/json'):
            return response
        if session.get('email'):
            # Never share authenticated HTML/JSON through an intermediary cache.
            response.cache_control.private = True
            response.cache_control.no_store = True
        response.vary.add('Accept-Encoding')
        if (request.method != 'GET' or response.status_code != 200 or response.is_streamed
                or response.direct_passthrough or response.headers.get('Content-Encoding')
                or request.accept_encodings.quality('gzip') <= 0):
            return response
        body = response.get_data()
        if len(body) >= 1024:
            compressed = gzip.compress(body, compresslevel=5, mtime=0)
            if len(compressed) < len(body):
                response.set_data(compressed)
                response.headers['Content-Encoding'] = 'gzip'
                etag, weak = response.get_etag()
                if etag:
                    response.set_etag(etag + '-gzip', weak=weak)
        return response
