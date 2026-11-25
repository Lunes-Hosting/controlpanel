"""Per-worker memory of failed background actions, so a scheduled job does not
repeat and report the same failure on every run."""

from collections import OrderedDict
from threading import Lock
from time import monotonic


class RetryThrottle:
    def __init__(self, retry_after=600, report_every=86400, max_entries=4096, clock=monotonic):
        self.retry_after = retry_after
        self.report_every = report_every
        self.max_entries = max_entries
        self.clock = clock
        self.entries = OrderedDict()
        self.lock = Lock()

    def _entry(self, key):
        # [earliest next attempt, earliest next report]
        entry = self.entries.setdefault(key, [0, 0])
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        return entry

    def begin(self, key):
        """Claim an attempt. False while an earlier one is running or failed recently."""
        now = self.clock()
        with self.lock:
            entry = self._entry(key)
            if now < entry[0]:
                return False
            entry[0] = now + self.retry_after
            return True

    def succeeded(self, key):
        with self.lock:
            self.entries.pop(key, None)

    def failed(self, key):
        """True when the failure should be reported: at first, then once per report_every."""
        now = self.clock()
        with self.lock:
            entry = self._entry(key)
            if now < entry[1]:
                return False
            entry[1] = now + self.report_every
            return True
