"""Deduplication of operator commands re-sent by network retries.

A retried POST carries the same client-generated command id as the original.
The first call runs the action and its outcome is remembered; any replay of
the same id within the retention window is answered from that outcome without
touching the equipment, so a coil change can never shear or book a ledger
entry twice.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.events import utcnow

#: A retried command arriving this long after the original is treated as new.
DEFAULT_WINDOW = timedelta(minutes=10)

#: Finished command records retained at most, to bound memory.
DEFAULT_LIMIT = 1000

#: Marker added to a replay reply so callers can see the command was suppressed.
REPLAY_MARKER = "replay"

Action = Callable[[], dict[str, object]]


class DuplicateCommand(RuntimeError):
    """Raised when the same command id is replayed while the first run is live."""

    def __init__(self, scope: str, command_id: str) -> None:
        super().__init__(f"command {command_id!r} for {scope!r} is already running")
        self.scope = scope
        self.command_id = command_id

    def to_dict(self) -> dict[str, str]:
        return {"code": "duplicate-command", "detail": str(self)}


@dataclass(slots=True)
class _Record:
    scope: str
    result: dict[str, object]
    at: datetime = field(default_factory=utcnow)


class Idempotency:
    """Remembers the outcome of the most recent commands keyed by command id."""

    def __init__(
        self,
        window: timedelta = DEFAULT_WINDOW,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self._lock = threading.Lock()
        self._window = window
        self._limit = limit
        self._running: dict[str, str] = {}
        self._done: dict[str, _Record] = {}

    def run(self, scope: str, command_id: str, action: Action) -> tuple[dict[str, object], bool]:
        """Run ``action`` once for a command id; return ``(result, replay)``.

        A replay never invokes ``action`` and carries the original result with
        :data:`REPLAY_MARKER` set. A command whose id is still executing is
        rejected with :class:`DuplicateCommand` rather than queued.
        """

        key = f"{scope}:{command_id}"
        with self._lock:
            self._evict(utcnow())
            if self._running.get(key) == scope:
                raise DuplicateCommand(scope, command_id)
            record = self._done.get(key)
            if record is not None and record.scope == scope:
                return dict(record.result), True
            self._running[key] = scope

        ok = False
        try:
            result = action()
            ok = True
        finally:
            with self._lock:
                self._running.pop(key, None)
                if ok:
                    self._done[key] = _Record(scope=scope, result=dict(result))
                    self._trim()
        return result, False

    def _evict(self, now: datetime) -> None:
        expired = [
            key
            for key, record in self._done.items()
            if now - record.at > self._window
        ]
        for key in expired:
            del self._done[key]

    def _trim(self) -> None:
        if len(self._done) <= self._limit:
            return
        ordered = sorted(self._done.items(), key=lambda item: item[1].at)
        for key, _ in ordered[: len(self._done) - self._limit]:
            del self._done[key]
