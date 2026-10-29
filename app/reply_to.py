"""reply_to allowlist. There is no built-in recipient address."""

from __future__ import annotations

import re

# Single address only. Display names, mailto:, and recipient lists are rejected.
_EMAIL = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
_MULTI_RECIPIENT = (",", ";", " ", "\t", "\n", "\r")


class ReplyToNotAllowed(Exception):
    """The reply_to value is missing from the server allowlist or is not one address."""


def is_exact_email(value: str) -> bool:
    return _EMAIL.fullmatch(value) is not None


def resolve_reply_to(
    value: object,
    allowlist: list[str],
    default: str | None,
) -> str:
    """Return the normalized recipient.

    Omitted or blank uses `default` from the environment. If that default is
    unset, or the address is not on the allowlist, the request is rejected.
    """
    if value is None:
        candidate = default
    elif isinstance(value, str):
        stripped = value.strip()
        candidate = default if stripped == "" else stripped
    else:
        raise ReplyToNotAllowed

    if candidate is None or not candidate.strip():
        raise ReplyToNotAllowed

    normalized = candidate.strip().lower()
    if normalized.startswith("mailto:"):
        raise ReplyToNotAllowed
    if any(separator in candidate for separator in _MULTI_RECIPIENT):
        raise ReplyToNotAllowed
    if not is_exact_email(normalized):
        raise ReplyToNotAllowed
    if normalized not in allowlist:
        raise ReplyToNotAllowed
    return normalized
