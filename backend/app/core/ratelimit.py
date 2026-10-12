"""Login throttling.

Partners reach the API through the portal's server, so every request arrives
from the same IP: a per-IP limit would be shared by all partners at once. The
IP limit is therefore only a coarse flood guard; the real brute-force guard is
per account (failed attempts per email).
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])

LOGIN_IP_LIMIT = "120/minute"


class LoginGuard:
    """Blocks an email after `max_failures` wrong passwords within `window_sec`.
    In-process (the API runs as one instance); a success clears the count."""

    def __init__(self, max_failures: int = 10, window_sec: int = 15 * 60) -> None:
        self.max_failures = max_failures
        self.window_sec = window_sec
        self._fails: dict[str, deque[float]] = {}
        self._lock = Lock()

    def _recent(self, email: str, now: float) -> deque[float]:
        q = self._fails.setdefault(email, deque())
        while q and now - q[0] > self.window_sec:
            q.popleft()
        return q

    def blocked(self, email: str) -> bool:
        with self._lock:
            return len(self._recent(email, time.monotonic())) >= self.max_failures

    def failed(self, email: str) -> None:
        with self._lock:
            self._recent(email, time.monotonic()).append(time.monotonic())

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._fails.pop(email, None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


login_guard = LoginGuard()
