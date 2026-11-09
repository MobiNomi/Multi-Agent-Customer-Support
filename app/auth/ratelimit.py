"""A small in-memory rate limiter: at most N events per key (an IP address) in a time window.

Kept in the server's memory, so the counts reset on restart and aren't shared between several
server processes. That's enough for one server; use Redis for more.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: int):
        self.max_events, self.window = max_events, window_seconds
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> Tuple[bool, int]:
        """Record one event. Returns (allowed, seconds until the next one would be allowed)."""
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.max_events:
                return False, int(self.window - (now - events[0])) + 1
            events.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
