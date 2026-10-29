from __future__ import annotations

import pytest

from app.auth import hmac_sha256_hex
from tests.conftest import Harness
from tests.support import HMAC_KEY, task_body

PINNED_HMAC = "bbb8be9666a8373efa71ffb4bbed69890ab4d7572cba1792c6bfede89cabdbef"


def test_hmac_is_sha256_of_timestamp_dot_raw_body() -> None:
    body = b"{}"
    timestamp = "1700000000"
    assert hmac_sha256_hex(HMAC_KEY, timestamp, body) == PINNED_HMAC


@pytest.mark.parametrize("offset", [0, 300, -300])
def test_timestamp_inside_window_is_accepted(harness: Harness, offset: int) -> None:
    response = harness.post(task_body(idempotency_key=f"idem-{offset}"), timestamp=int(harness.now) + offset)
    assert response.status_code == 202


@pytest.mark.parametrize("offset", [301, -301])
def test_timestamp_outside_window_is_unauthorized(harness: Harness, offset: int) -> None:
    response = harness.post(b"not-json", timestamp=int(harness.now) + offset)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert harness.notifier.events == []


@pytest.mark.parametrize(
    "drop",
    [
        ("Authorization",),
        ("X-Cue-Timestamp",),
        ("X-Cue-Signature",),
    ],
)
def test_bearer_and_hmac_are_both_required(harness: Harness, drop: tuple[str, ...]) -> None:
    response = harness.post(task_body(), drop=drop)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert harness.notifier.events == []


def test_wrong_signature_is_unauthorized(harness: Harness) -> None:
    response = harness.post(task_body(), signature="ab" * 32)
    assert response.status_code == 401
    assert harness.notifier.events == []


def test_sha256_prefix_is_not_the_signature_format(harness: Harness) -> None:
    body = task_body()
    timestamp = str(int(harness.now))
    prefixed = "sha256=" + hmac_sha256_hex(HMAC_KEY, timestamp, body)
    response = harness.post(body, signature=prefixed)
    assert response.status_code == 401


def test_same_signature_replay_is_unauthorized(harness: Harness) -> None:
    body = task_body()
    first = harness.post(body)
    assert first.status_code == 202
    replay = harness.post(body)
    assert replay.status_code == 401
    assert replay.json() == {"error": "unauthorized"}
    assert len(harness.notifier.events) == 1


def test_uppercase_signature_is_the_same_signature(harness: Harness) -> None:
    body = task_body()
    timestamp = str(int(harness.now))
    signature = hmac_sha256_hex(HMAC_KEY, timestamp, body).upper()
    first = harness.post(body, signature=signature)
    assert first.status_code == 202
    second = harness.post(body, signature=signature.lower())
    assert second.status_code == 401


def test_secondary_hmac_key_is_accepted(harness_factory) -> None:
    harness = harness_factory(hmac_secondary="secondary-hmac-key")
    response = harness.post(task_body(), hmac_key="secondary-hmac-key")
    assert response.status_code == 202


def test_secondary_bearer_is_accepted(harness_factory) -> None:
    harness = harness_factory(token_secondary="secondary-token")
    response = harness.post(task_body(), token="secondary-token")
    assert response.status_code == 202
