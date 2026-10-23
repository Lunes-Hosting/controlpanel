"""Direct webhook transport: validate each delivery, pin DNS, never follow redirects."""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit


def destination(url):
    if not isinstance(url, str) or len(url) > 1024 or any(ord(c) < 33 for c in url):
        raise ValueError('Invalid webhook URL.')
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError('Use an HTTP(S) URL without credentials or fragments.')
    port = parts.port if parts.port is not None else (443 if parts.scheme == 'https' else 80)
    if not 1 <= port <= 65535:
        raise ValueError('Invalid destination port.')
    host = parts.hostname.encode('idna').decode('ascii')
    return parts, host, port


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError('Destination could not be resolved.')
    for family, kind, protocol, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        mapped = getattr(ip, 'ipv4_mapped', None)
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or (mapped is not None and not mapped.is_global)
                or str(ip) == '168.63.129.16' or '%' in address[0]
                or (ip.version == 6 and (ip.sixtofour is not None or ip.teredo is not None
                    or ip in ipaddress.ip_network('64:ff9b::/96')
                    or ip in ipaddress.ip_network('64:ff9b:1::/48')))):
            raise ValueError('Destination is in a restricted network.')
    return addresses


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, addresses, secure):
        super().__init__(host, port, timeout=3)
        self.addresses = addresses
        self.secure = secure

    def connect(self):
        # Never pass a hostname to connect/create_connection after validation.
        last_error = None
        for family, kind, protocol, _, address in self.addresses:
            sock = socket.socket(family, kind, protocol)
            try:
                sock.settimeout(3)
                sock.connect(address)
                if self.secure:
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
                sock.settimeout(5)
                self.sock = sock
                return
            except OSError as exc:
                sock.close()
                last_error = exc
        raise last_error or OSError('Destination unavailable.')


def post_webhook(url, payload):
    parts, host, port = destination(url)
    addresses = public_addresses(host, port)
    connection = PinnedConnection(host, port, addresses, parts.scheme == 'https')
    response = None
    try:
        path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        connection.request('POST', path, body=json.dumps(payload).encode('utf-8'),
                           headers={'Content-Type': 'application/json', 'User-Agent': 'LunesHosting-Webhook/1.0'})
        response = connection.getresponse()
        # Deliberately do not read bodies, follow Location, or use environment proxies.
        return response.status, response.getheader('Retry-After')
    finally:
        if response is not None:
            response.close()
        connection.close()
