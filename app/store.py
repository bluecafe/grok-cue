"""Process-local idempotency. Restarting the process forgets prior acceptances."""

from __future__ import annotations

import copy
import hashlib
import threading
from dataclasses import dataclass


class IdempotencyConflict(Exception):
    """The idempotency key was reused with a different raw body."""


@dataclass(slots=True)
class _Entry:
    body_sha256: str
    response: dict[str, object]


@dataclass(frozen=True, slots=True)
class RegisterResult:
    response: dict[str, object]
    created: bool


class IdempotencyStore:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def register(
        self,
        key: str,
        raw_body: bytes,
        response: dict[str, object],
    ) -> RegisterResult:
        """Store the first acceptance for this key.

        The same key and the same raw body return that stored response.
        The same key and any other bytes raise IdempotencyConflict.
        Callers must not notify again when `created` is false.
        """
        digest = hashlib.sha256(raw_body).hexdigest()
        with self._lock:
            current = self._entries.get(key)
            if current is None:
                saved = copy.deepcopy(response)
                self._entries[key] = _Entry(body_sha256=digest, response=saved)
                return RegisterResult(response=copy.deepcopy(saved), created=True)
            if current.body_sha256 != digest:
                raise IdempotencyConflict
            return RegisterResult(
                response=copy.deepcopy(current.response),
                created=False,
            )
