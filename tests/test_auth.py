from __future__ import annotations

import logging

import pytest

from app.auth import hmac_sha256_hex
from tests.conftest import Harness
from tests.support import HMAC_KEY, TOKEN, task_body

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


def test_auth_reason_log_has_no_secret(
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="grok_cue")
    bad_token = "client-supplied-token"
    bad_signature = "ab" * 32
    summary = "SUMMARY_SHOULD_NOT_BE_LOGGED"
    denied = harness.post(
        task_body(summary=summary, idempotency_key="idem-auth-reason"),
        token=bad_token,
        signature=bad_signature,
        timestamp=int(harness.now) + 1,
    )
    skew = harness.post(
        task_body(summary=summary, idempotency_key="idem-auth-skew"),
        timestamp=int(harness.now) + 301,
    )
    missing_ts = harness.post(
        task_body(summary=summary, idempotency_key="idem-auth-missing-ts"),
        drop=("X-Cue-Timestamp",),
    )
    ok = harness.post(task_body(summary=summary, idempotency_key="idem-auth-ok"))
    replay = harness.post(task_body(summary=summary, idempotency_key="idem-auth-ok"))
    assert denied.status_code == 401
    assert skew.status_code == 401
    assert missing_ts.status_code == 401
    assert ok.status_code == 202
    assert replay.status_code == 401
    text = caplog.text
    assert "reason=bearer_rejected,signature_rejected" in text
    assert "reason=timestamp_out_of_window" in text
    assert "reason=missing_timestamp" in text
    assert "reason=replay" in text
    assert TOKEN not in text
    assert HMAC_KEY not in text
    assert bad_token not in text
    assert bad_signature not in text
    assert summary not in text
