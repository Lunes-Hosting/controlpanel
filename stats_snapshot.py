"""One background statistics refresh per worker, with a last successful snapshot."""

from copy import deepcopy
from threading import Lock, Thread
from time import monotonic
import logging


class StatsSnapshot:
    def __init__(self, loader, ttl=60, retry_delay=15):
        self.loader = loader
        self.ttl = ttl
        self.retry_delay = retry_delay
        self.lock = Lock()
        self.data = None
        self.updated = None
        self.retry_at = 0
        self.refreshing = False
        self.failed = False

    def get(self):
        with self.lock:
            now = monotonic()
            stale = self.updated is None or now - self.updated >= self.ttl
            start = stale and not self.refreshing and now >= self.retry_at
            if start:
                self.refreshing = True
            result = {'data': deepcopy(self.data), 'refreshing': self.refreshing,
                      'stale': stale, 'error': self.failed}
        if start:
            try:
                Thread(target=self._refresh, daemon=True).start()
            except RuntimeError:
                self._failed()
                result['error'] = True
                result['refreshing'] = False
        return result

    def _failed(self):
        with self.lock:
            self.failed = True
            self.retry_at = monotonic() + self.retry_delay
            self.refreshing = False

    def _refresh(self):
        try:
            data = self.loader()
            if data is None:
                raise ValueError('Statistics unavailable')
        except Exception as exc:
            logging.getLogger(__name__).warning('Statistics refresh failed: %s', type(exc).__name__)
            self._failed()
        else:
            with self.lock:
                self.data = data
                self.updated = monotonic()
                self.failed = False
                self.refreshing = False
