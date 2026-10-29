"""Bearer plus HMAC-SHA256 over `{timestamp}.{raw_body}`."""

from __future__ import annotations

import hashlib
import hmac
import threading

# |now - timestamp| must be within five minutes. Outside that, reject before JSON parsing.
SKEW_SECONDS = 300
# Remember a spent signature slightly longer than the accept window.
REPLAY_TTL_SECONDS = 600


def hmac_sha256_hex(key: str, timestamp: str, raw_body: bytes) -> str:
    message = timestamp.encode("ascii") + b"." + raw_body
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def parse_unix_timestamp(header: str | None) -> int | None:
    if header is None:
        return None
    value = header.strip()
    if not value.isascii() or not value.isdigit() or len(value) > 12:
        return None
    return int(value)


def parse_bearer(header: str | None) -> str | None:
    if header is None:
        return None
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return None
    token = token.strip()
    if not token or any(char.isspace() for char in token):
        return None
    return token


def secret_equal(provided: str, expected: str) -> bool:
    # Hash both sides so the compare length does not track the secret length.
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def bearer_matches(provided: str, tokens: list[str]) -> bool:
    ok = False
    for token in tokens:
        ok = secret_equal(provided, token) or ok
    return ok


def _digest_equal(provided: str, expected: str) -> bool:
    if len(provided) != len(expected):
        return False
    return hmac.compare_digest(provided, expected)


def signature_matches(
    provided: str | None,
    keys: list[str],
    timestamp: str,
    raw_body: bytes,
) -> bool:
    candidate = (provided or "").strip().lower()
    ok = False
    for key in keys:
        expected = hmac_sha256_hex(key, timestamp, raw_body)
        ok = _digest_equal(candidate, expected) or ok
    return ok


class ReplayCache:
    """Spend each fully authenticated signature once.

    A network retry keeps the body and mints a new timestamp and HMAC.
    Re-sending the previous signature is rejected even inside the five-minute window.
    """

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def seen_or_remember(self, signature: str, timestamp: int, now: float) -> bool:
        with self._lock:
            self._prune(now)
            if signature in self._seen:
                return True
            self._seen[signature] = timestamp
            return False

    def _prune(self, now: float) -> None:
        cutoff = int(now) - REPLAY_TTL_SECONDS
        stale = [signature for signature, ts in self._seen.items() if ts < cutoff]
        for signature in stale:
            del self._seen[signature]
