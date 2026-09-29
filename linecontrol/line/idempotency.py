"""Idempotency guard for operator commands that a flaky network may retry.

A coil change cuts the strip, so the *same* logical command must only ever
take effect once.  Two ways of recognising a retried command are supported:

* an explicit idempotency token supplied by the caller (header or query
  parameter), which stays valid for a long window; and
* when no token is present, a short window in which another token-less
  coil-change request is treated as the retry of the first one.

Only commands that actually completed are remembered: a command that failed
leaves nothing behind, so its retry is allowed to run again.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import timedelta

from ..domain.records import utcnow

#: A token supplied by the requester stays recognised for this long.
TOKEN_TTL = timedelta(seconds=600)

#: Token-less retries are recognised only within this short window.
BARE_TTL = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """Whether a command may run, and why it was held back."""

    allow: bool
    duplicate: bool = False
    key: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"allow": self.allow, "duplicate": self.duplicate, "key": self.key}


@dataclass(slots=True)
class _Seen:
    completed_at: object
    ttl: timedelta


class IdempotencyGuard:
    """Remembers the most recent completed commands, keyed by operation."""

    def __init__(self, token_ttl: timedelta = TOKEN_TTL, bare_ttl: timedelta = BARE_TTL) -> None:
        self._token_ttl = token_ttl
        self._bare_ttl = bare_ttl
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, str], _Seen] = {}
        self.duplicates = 0

    def begin(self, operation: str, token: str = "") -> GuardDecision:
        """Claim ``operation`` for this caller.

        Returns ``allow`` when the command body should run and ``duplicate``
        when the same logical command already completed recently (or is still
        running from a concurrent delivery).
        """

        now = utcnow()
        with self._lock:
            self._prune(now)
            key = (operation, token)
            if key in self._seen:
                self.duplicates += 1
                return GuardDecision(False, True, token)
            # Bare (token-less) requests collapse onto one another while the
            # most recent one is still fresh: there is nothing else to tell
            # two quick retries apart.
            ttl = self._token_ttl if token else self._bare_ttl
            if not token:
                for (seen_op, seen_token), seen in self._seen.items():
                    if seen_op == operation and not seen_token:
                        if now - seen.completed_at < self._bare_ttl:
                            self.duplicates += 1
                            return GuardDecision(False, True, "")
            self._seen[key] = _Seen(completed_at=now, ttl=ttl)
            return GuardDecision(True, False, token)

    def commit(self, operation: str, token: str = "") -> None:
        """Stamp the command as completed so retries are recognised afterwards."""

        with self._lock:
            entry = self._seen.get((operation, token))
            if entry is not None:
                entry.completed_at = utcnow()

    def discard(self, operation: str, token: str = "") -> None:
        """Forget a command that did not complete, freeing its retry."""

        with self._lock:
            self._seen.pop((operation, token), None)

    def is_duplicate(self, operation: str, token: str = "") -> bool:
        with self._lock:
            self._prune(utcnow())
            if (operation, token) in self._seen:
                return True
            if token:
                return False
            now = utcnow()
            return any(
                op == operation
                and not key_token
                and now - seen.completed_at < self._bare_ttl
                for (op, key_token), seen in self._seen.items()
            )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._prune(utcnow())
            return {
                "tracked": len(self._seen),
                "duplicates": self.duplicates,
                "tokenTtlSec": int(self._token_ttl.total_seconds()),
                "bareTtlSec": int(self._bare_ttl.total_seconds()),
            }

    def _prune(self, now) -> None:
        expired = [
            key
            for key, seen in self._seen.items()
            if now - seen.completed_at >= seen.ttl
        ]
        for key in expired:
            del self._seen[key]
