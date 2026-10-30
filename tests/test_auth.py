from __future__ import annotations

import logging

import pytest

from app.auth import hmac_v2_hex
from tests.conftest import Harness
from tests.support import HMAC_KEY, TOKEN, sign, sign_v2, task_body

PINNED_V2 = "7d605e7bfd76f7ab92a251d257f12982aa9b73f7f1c910af6bbfc2243a84dedc"


def test_v2_hmac_binds_method_path_timestamp_and_body() -> None:
    body = b"{}"
    timestamp = "1700000000"
    assert hmac_v2_hex(HMAC_KEY, "POST", "/v1/cue/tasks", timestamp, body) == PINNED_V2


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
    prefixed = "sha256=" + sign_v2(HMAC_KEY, "POST", "/v1/cue/tasks", timestamp, body)
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
    signature = sign_v2(HMAC_KEY, "POST", "/v1/cue/tasks", timestamp, body).upper()
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


def test_v2_signature_binds_method_and_path(harness: Harness) -> None:
    ts = int(harness.now)
    accepted = harness.post(
        task_body(idempotency_key="idem-v2-ok"),
        timestamp=ts,
        signature_version="2",
    )
    assert accepted.status_code == 202

    method_body = task_body(idempotency_key="idem-v2-method", report_seq=2)
    method_ts = str(ts + 1)
    bad_method = sign_v2(HMAC_KEY, "GET", "/v1/cue/tasks", method_ts, method_body)
    rejected_method = harness.post(
        method_body,
        timestamp=ts + 1,
        signature=bad_method,
        signature_version="2",
    )
    assert rejected_method.status_code == 401

    path_body = task_body(idempotency_key="idem-v2-path", report_seq=3)
    path_ts = str(ts + 2)
    bad_path = sign_v2(HMAC_KEY, "POST", "/v1/other", path_ts, path_body)
    rejected_path = harness.post(
        path_body,
        timestamp=ts + 2,
        signature=bad_path,
        signature_version="2",
    )
    assert rejected_path.status_code == 401

    v1_body = task_body(idempotency_key="idem-v2-material", report_seq=4)
    v1_sig = sign(HMAC_KEY, str(ts + 3), v1_body)
    rejected_v1 = harness.post(
        v1_body,
        timestamp=ts + 3,
        signature=v1_sig,
        signature_version="2",
    )
    assert rejected_v1.status_code == 401

    naked = task_body(idempotency_key="idem-v2-naked", report_seq=5)
    naked_sig = sign_v2(HMAC_KEY, "POST", "/v1/cue/tasks", str(ts + 4), naked)
    rejected_naked = harness.post(naked, timestamp=ts + 4, signature=naked_sig, omit_version=True)
    assert rejected_naked.status_code == 401
    assert rejected_naked.json() == {"error": "unauthorized"}


def test_v1_header_is_401(harness: Harness) -> None:
    body = task_body(idempotency_key="idem-reject-v1")
    timestamp = str(int(harness.now))
    signature = sign(HMAC_KEY, timestamp, body)
    response = harness.post(body, signature=signature, signature_version="1")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert signature not in response.text
    assert HMAC_KEY not in response.text
    assert harness.notifier.events == []


def test_missing_signature_version_is_401(harness: Harness) -> None:
    body = task_body(idempotency_key="idem-reject-missing")
    timestamp = str(int(harness.now))
    signature = sign(HMAC_KEY, timestamp, body)
    response = harness.post(body, signature=signature, omit_version=True)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert signature not in response.text
    assert HMAC_KEY not in response.text
    assert harness.notifier.events == []


def test_v2_header_is_202(harness: Harness) -> None:
    response = harness.post(task_body(idempotency_key="idem-accept-v2"), signature_version="2")
    assert response.status_code == 202
    assert harness.notifier.events[0][3] == 2


def test_unknown_signature_version_401(
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="grok_cue")
    leaked = "version-header-secret"
    versions = ("1", "3", "v2", "0", "", leaked)
    for offset, version in enumerate(versions):
        response = harness.post(
            task_body(idempotency_key=f"idem-unknown-{offset}"),
            timestamp=int(harness.now) + offset,
            signature_version=version,
        )
        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}
    assert "unknown_signature_version" in caplog.text
    assert leaked not in caplog.text
    assert harness.notifier.events == []
