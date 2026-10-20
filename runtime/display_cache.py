"""Small per-worker cache for display data, never authorization decisions."""

from collections import OrderedDict
from copy import deepcopy
from threading import Lock
from time import monotonic


class DisplayCache:
    def __init__(self, max_entries=256):
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.lock = Lock()
        self.loads = [Lock() for _ in range(32)]
        self.generation = 0

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.generation += 1

    def get_or_load(self, key, loader, ttl=10):
        # Striped locks bound memory while collapsing simultaneous cache misses.
        with self.loads[hash(key) % len(self.loads)]:
            with self.lock:
                generation = self.generation
                entry = self.entries.get(key)
                if entry and entry[0] > monotonic():
                    self.entries.move_to_end(key)
                    return deepcopy(entry[1])
            value = loader()
            if value is not None:
                with self.lock:
                    if generation == self.generation:
                        self.entries[key] = (monotonic() + ttl, deepcopy(value))
                        self.entries.move_to_end(key)
                        while len(self.entries) > self.max_entries:
                            self.entries.popitem(last=False)
            return value


display_cache = DisplayCache()
