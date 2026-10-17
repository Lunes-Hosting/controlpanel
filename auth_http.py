"""Connection reuse and a short-lived cache for authentication dependencies."""
import logging
import threading
from time import monotonic
import requests

class AuthRequests:
    """One connection pool per web worker thread; never retry account mutations."""
    def __init__(self):
        self.local = threading.local()
    def request(self, method, url, **kwargs):
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
        return self.local.session.request(method, url, **kwargs)
    def get(self, url, **kwargs): return self.request('GET', url, **kwargs)
    def post(self, url, **kwargs): return self.request('POST', url, **kwargs)
    def patch(self, url, **kwargs): return self.request('PATCH', url, **kwargs)

auth_requests = AuthRequests()

def verify_turnstile(token, secret):
    if not isinstance(token, str) or not token or len(token) > 4096:
        return False
    response = auth_requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify',
                                  data={'secret': secret, 'response': token}, timeout=(2, 5))
    response.raise_for_status()
    result = response.json()
    return isinstance(result, dict) and result.get('success') is True

class BlockedEmailList:
    """Reuse the blacklist, retaining the last good copy during an outage."""
    def __init__(self):
        self.lock = threading.Lock()
        self.warming = threading.Lock()
        self.entries = ()
        self.expires = 0
    def preload(self):
        # Only one warm-up thread, even when many users open the signup page.
        if monotonic() < self.expires or not self.warming.acquire(blocking=False):
            return
        def run():
            try:
                self.get()
            finally:
                self.warming.release()
        try:
            threading.Thread(target=run, daemon=True, name='signup-blocklist').start()
        except RuntimeError:
            self.warming.release()

    def get(self):
        with self.lock:
            if monotonic() < self.expires:
                return self.entries
            try:
                response = auth_requests.get('https://lunes.host/blockedemails.txt', timeout=(2, 4))
                response.raise_for_status()
                self.entries = tuple(line.strip().lower() for line in response.text.splitlines() if line.strip())
                self.expires = monotonic() + 900
            except (requests.RequestException, ValueError):
                # Preserve the previous fail-open behavior on a cold outage, but
                # keep enforcing any list already fetched and avoid repeated waits.
                self.expires = monotonic() + 60
                logging.getLogger(__name__).warning('Email blacklist refresh unavailable; retaining cached entries')
            return self.entries

blocked_email_list = BlockedEmailList()
