"""Bound login work without requiring deployment-specific proxy configuration."""

import ipaddress
import re
import hashlib
from flask import request

_email = re.compile(r"[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+\.[^\s@\x00-\x1f\x7f]+\Z")


def peer_ip(req=None):
    """Connection address, unaffected by cookies or forwarded-IP headers."""
    req = req if req is not None else request
    try:
        peer = ipaddress.ip_address(req.remote_addr)
    except (ValueError, TypeError):
        return 'unknown'
    return str(peer)


def client_ip(req=None):
    """Preserve the existing Cloudflare/reverse-proxy address compatibility.

    These headers are not proof of identity. Login abuse protection separately
    uses peer_ip(), so changing a forwarding header cannot reset those limits.
    """
    req = req if req is not None else request
    for candidate in (req.headers.get('CF-Connecting-IP', ''),
                      req.headers.get('X-Forwarded-For', '').split(',', 1)[0].strip()):
        if len(candidate) > 45:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return peer_ip(req)


def login_limit_key():
    # Partition users sharing a proxy without trusting spoofable headers.
    # Do not parse large/unknown-sized request bodies to compute a limit key.
    email = ''
    if (request.content_length is not None and request.content_length <= 16 * 1024
            and request.mimetype in ('application/x-www-form-urlencoded', 'multipart/form-data')):
        email = request.form.get('email', '').strip().lower()[:191]
    digest = hashlib.sha256(email.encode('utf-8')).hexdigest()
    return f'{peer_ip()}:{digest}'


def login_credentials(req):
    """Reject malformed/oversized input before CAPTCHA, SQL or bcrypt."""
    if req.content_length is not None and req.content_length > 16 * 1024:
        return None
    if req.mimetype not in ('application/x-www-form-urlencoded', 'multipart/form-data'):
        return None
    if len(req.form.getlist('email')) != 1 or len(req.form.getlist('password')) != 1:
        return None
    email = req.form.get('email', '').strip().lower()
    password = req.form.get('password', '')
    captcha = req.form.get('g-recaptcha-response', '')
    if (len(email) > 191 or not _email.fullmatch(email) or not password
            or len(password.encode('utf-8')) > 1024 or len(captcha) > 4096):
        return None
    return email, password
