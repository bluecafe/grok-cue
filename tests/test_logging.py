from __future__ import annotations

import logging

import pytest

from app.redact import SecretRedactingFilter
from tests.conftest import Harness
from tests.support import HMAC_KEY, TOKEN, task_body


def test_filter_redacts_configured_secrets() -> None:
    redactor = SecretRedactingFilter(["super-secret-token"])
    record = logging.LogRecord(
        name="grok_cue",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="auth %s",
        args=("super-secret-token",),
        exc_info=None,
    )
    assert redactor.filter(record) is True
    assert record.getMessage() == "auth [redacted]"


def test_request_logs_omit_secrets_and_summary(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="grok_cue")
    body = task_body(summary="SUMMARY_SHOULD_NOT_BE_LOGGED")
    ok = harness.post(body)
    denied = harness.post(
        task_body(idempotency_key="idem-denied", summary="SUMMARY_SHOULD_NOT_BE_LOGGED"),
        timestamp=int(harness.now) + 1,
        token="client-supplied-token",
    )
    assert ok.status_code == 202
    assert denied.status_code == 401
    text = caplog.text
    assert TOKEN not in text
    assert HMAC_KEY not in text
    assert "SUMMARY_SHOULD_NOT_BE_LOGGED" not in text
    assert "client-supplied-token" not in text
    assert "notify=skipped" in text
