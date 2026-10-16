"""Per-request timing, sent back as a `Server-Timing` header (visible in the
browser's Network tab) so slow pages can be traced to the MIS database, the
CRM or our own code without guessing.

    Server-Timing: auth;dur=210, crm;dur=640;desc="2 queries", app;dur=905
"""
from __future__ import annotations

import time
from contextvars import ContextVar

_spans: ContextVar[dict[str, list[float]] | None] = ContextVar("mis_timing", default=None)


def start() -> None:
    _spans.set({})


def add(name: str, seconds: float) -> None:
    spans = _spans.get()
    if spans is not None:
        spans.setdefault(name, []).append(seconds)


class span:
    """`with timing.span("crm"): ...` adds the block's time under `name`."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> None:
        self.t = time.perf_counter()

    def __exit__(self, *exc) -> None:
        add(self.name, time.perf_counter() - self.t)


def header(total: float) -> str:
    parts = []
    for name, times in (_spans.get() or {}).items():
        part = f"{name};dur={sum(times) * 1000:.0f}"
        if len(times) > 1:
            part += f';desc="{len(times)} calls"'
        parts.append(part)
    parts.append(f"app;dur={total * 1000:.0f}")
    return ", ".join(parts)
