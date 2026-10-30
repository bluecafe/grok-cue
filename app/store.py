"""Process-local idempotency. Restarting the process forgets prior acceptances."""

from __future__ import annotations

import copy
import hashlib
import threading
from dataclasses import dataclass


class IdempotencyConflict(Exception):
    """The idempotency key was reused with a different raw body."""


class SequenceConflict(Exception):
    """The same handoff generation was sent with a different raw body."""


def is_older_generation(
    current: tuple[int, int] | None,
    attempt: int,
    report_seq: int,
) -> bool:
    """True when (attempt, report_seq) is strictly before the stored generation."""
    if current is None:
        return False
    return (attempt, report_seq) < current


@dataclass(slots=True)
class _Entry:
    body_sha256: str
    response: dict[str, object]


@dataclass(frozen=True, slots=True)
class RegisterResult:
    response: dict[str, object]
    created: bool
    enqueue: bool = True


class IdempotencyStore:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._sequences: dict[tuple[str, int, int], tuple[str, str]] = {}
        self._generation: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def current_generation(self, handoff_id: str) -> tuple[int, int] | None:
        with self._lock:
            return self._generation.get(handoff_id)

    def register(
        self,
        key: str,
        raw_body: bytes,
        response: dict[str, object],
        *,
        handoff_id: str,
        attempt: int,
        report_seq: int,
    ) -> RegisterResult:
        """Store the first acceptance for this key.

        The same key and the same raw body return that stored response.
        The same key and any other bytes raise IdempotencyConflict.
        The same (handoff_id, attempt, report_seq) and any other bytes raise
        SequenceConflict. An older generation is stored and not enqueued.
        Callers must not notify again when `created` is false.
        """
        digest = hashlib.sha256(raw_body).hexdigest()
        slot = (handoff_id, attempt, report_seq)
        with self._lock:
            current = self._entries.get(key)
            if current is not None:
                if current.body_sha256 != digest:
                    raise IdempotencyConflict
                return RegisterResult(
                    response=copy.deepcopy(current.response),
                    created=False,
                    enqueue=False,
                )
            sequence = self._sequences.get(slot)
            if sequence is not None:
                stored_digest, stored_key = sequence
                if stored_digest != digest:
                    raise SequenceConflict
                original = self._entries[stored_key]
                saved = copy.deepcopy(original.response)
                self._entries[key] = _Entry(body_sha256=digest, response=copy.deepcopy(saved))
                return RegisterResult(response=saved, created=False, enqueue=False)
            saved = copy.deepcopy(response)
            self._entries[key] = _Entry(body_sha256=digest, response=copy.deepcopy(saved))
            self._sequences[slot] = (digest, key)
            cursor = self._generation.get(handoff_id)
            older = is_older_generation(cursor, attempt, report_seq)
            if not older:
                self._generation[handoff_id] = (attempt, report_seq)
            return RegisterResult(
                response=copy.deepcopy(saved),
                created=True,
                enqueue=not older,
            )
