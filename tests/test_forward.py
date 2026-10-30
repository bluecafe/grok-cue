"""Bridge schedules a Grok forward without turning acceptance into execution."""

from __future__ import annotations

import asyncio
import json

import httpx2 as httpx
import pytest

from app.forward import (
    FORWARD_FIELDS,
    FORWARD_TIMEOUT_SECONDS,
    GrokForwarder,
    classify_http_status,
    forward_headers,
)
from tests.conftest import Harness, build_harness
from tests.support import HANDOFF_ID, REPO, START_SHA, task_body

GROK_URL = "https://grok.example/hook"
GROK_KEY = "grok-webhook-key-should-not-appear-in-logs"


class Capture:
    def __init__(
        self,
        *,
        status: int = 204,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.error = error
        self.delay = delay

    def transport(self) -> httpx.MockTransport:
        capture = self

        async def handler(request: httpx.Request) -> httpx.Response:
            if capture.delay:
                await asyncio.sleep(capture.delay)
            if capture.error is not None:
                raise capture.error
            capture.requests.append(request)
            return httpx.Response(capture.status)

        return httpx.MockTransport(handler)


def _harness(
    capture: Capture,
    *,
    url: str | None = GROK_URL,
    key: str | None = GROK_KEY,
    header: str | None = None,
    timeout: float = FORWARD_TIMEOUT_SECONDS,
) -> Harness:
    settings_kwargs = {
        "grok_webhook_url": url,
        "grok_webhook_key": key,
        "grok_webhook_key_header": header,
    }
    # Build settings inside build_harness, then wrap the same values in the forwarder.
    probe = build_harness(**settings_kwargs)
    probe.http.close()
    forwarder = GrokForwarder(
        probe.settings,
        timeout=timeout,
        transport=capture.transport(),
    )
    return build_harness(**settings_kwargs, forwarder=forwarder)


def test_forward_timeout_constant_is_five_seconds() -> None:
    assert FORWARD_TIMEOUT_SECONDS == 5.0


def test_successful_forward_is_scheduled_and_not_execution() -> None:
    capture = Capture(status=204)
    harness = _harness(capture)
    response = harness.post(task_body())
    assert response.status_code == 202
    body = response.json()
    assert body["handoff_id"] == HANDOFF_ID
    assert body["status"] == "received"
    assert body["executed"] is False
    assert body["forwarded"] is True
    assert len(capture.requests) == 1


def test_unset_url_or_key_skips_forward() -> None:
    for kwargs in (
        {"url": None, "key": GROK_KEY},
        {"url": GROK_URL, "key": None},
        {"url": "", "key": GROK_KEY},
        {"url": "  ", "key": "  "},
    ):
        capture = Capture()
        harness = _harness(capture, **kwargs)
        response = harness.post(task_body())
        assert response.status_code == 202
        assert response.json()["forwarded"] is False
        assert response.json()["executed"] is False
        assert capture.requests == []


def test_receiver_http_error_keeps_202(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="grok_cue")
    capture = Capture(status=500)
    harness = _harness(capture)
    response = harness.post(task_body(summary="SUMMARY_NOT_FOR_GROK_LOG"))
    assert response.status_code == 202
    assert response.json()["forwarded"] is True
    assert response.json()["executed"] is False
    assert len(capture.requests) == 1
    assert "outcome=http status=500" in caplog.text
    assert GROK_KEY not in caplog.text
    assert "SUMMARY_NOT_FOR_GROK_LOG" not in caplog.text


def test_receiver_timeout_keeps_202(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="grok_cue")
    capture = Capture(delay=1)
    harness = _harness(capture, timeout=0.05)
    response = harness.post(task_body())
    assert response.status_code == 202
    assert response.json()["forwarded"] is True
    assert response.json()["executed"] is False
    assert capture.requests == []
    assert "outcome=timeout" in caplog.text
    assert GROK_KEY not in caplog.text


def test_duplicate_body_does_not_forward_again() -> None:
    capture = Capture()
    harness = _harness(capture)
    body = task_body()
    first = harness.post(body, timestamp=int(harness.now))
    second = harness.post(body, timestamp=int(harness.now) + 1)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["forwarded"] is True
    assert second.json()["forwarded"] is False
    assert second.json()["executed"] is False
    assert second.json()["handoff_id"] == first.json()["handoff_id"]
    assert len(capture.requests) == 1
    assert len(harness.notifier.events) == 1


def test_forward_payload_is_the_handoff_whitelist() -> None:
    capture = Capture()
    harness = _harness(capture)
    response = harness.post(
        task_body(
            summary="short result",
            links=["https://github.com/example/repo/pull/4"],
            requested_by="example-requester",
        )
    )
    assert response.status_code == 202
    sent = json.loads(capture.requests[0].content)
    assert tuple(sent) == FORWARD_FIELDS
    assert sent["handoff_id"] == HANDOFF_ID
    assert sent["repo"] == REPO
    assert sent["status"] == "completed"
    assert sent["summary"] == "short result"
    assert sent["links"] == ["https://github.com/example/repo/pull/4"]
    assert sent["next_owner"] == "example"
    assert sent["idempotency_key"] == "idem-1"
    assert sent["github_ref"]["start_sha"] == START_SHA
    for key in ("schema_version", "title", "created_at", "reply_to", "requested_by", "executed", "forwarded"):
        assert key not in sent


def test_forward_fields_are_accepted_fields() -> None:
    from app.models import CueTaskRequest

    assert len(FORWARD_FIELDS) == 8
    assert set(FORWARD_FIELDS) <= set(CueTaskRequest.model_fields)


def test_default_forward_header_is_bearer() -> None:
    assert forward_headers(GROK_KEY, None) == {"Authorization": f"Bearer {GROK_KEY}"}
    assert forward_headers(GROK_KEY, "") == {"Authorization": f"Bearer {GROK_KEY}"}
    capture = Capture()
    harness = _harness(capture)
    assert harness.post(task_body()).status_code == 202
    raw = [
        f"{name.decode()}: {value.decode()}"
        for name, value in capture.requests[0].headers.raw
    ]
    assert f"Authorization: Bearer {GROK_KEY}" in raw


def test_forward_outcome_classification(caplog: pytest.LogCaptureFixture) -> None:
    assert classify_http_status(200) == "delivered"
    assert classify_http_status(204) == "delivered"
    assert classify_http_status(408) == "retryable"
    assert classify_http_status(425) == "retryable"
    assert classify_http_status(429) == "retryable"
    assert classify_http_status(500) == "retryable"
    assert classify_http_status(599) == "retryable"
    assert classify_http_status(400) == "dead"
    assert classify_http_status(401) == "dead"
    assert classify_http_status(404) == "dead"
    assert classify_http_status(302) == "dead"

    caplog.set_level("INFO", logger="grok_cue")
    for status, outcome_class in ((204, "delivered"), (400, "dead"), (503, "retryable")):
        capture = Capture(status=status)
        harness = _harness(capture)
        response = harness.post(
            task_body(
                idempotency_key=f"idem-class-{status}",
                summary="SUMMARY_NOT_FOR_GROK_LOG",
            )
        )
        assert response.status_code == 202
        assert response.json()["forwarded"] is True
        assert f"outcome=http status={status} class={outcome_class}" in caplog.text
        harness.http.close()

    connect = Capture(error=httpx.ConnectError("down"))
    connect_harness = _harness(connect)
    connect_response = connect_harness.post(task_body(idempotency_key="idem-class-connect"))
    assert connect_response.status_code == 202
    assert "outcome=connection_error class=retryable" in caplog.text
    assert connect.requests == []
    connect_harness.http.close()

    assert GROK_KEY not in caplog.text
    assert "SUMMARY_NOT_FOR_GROK_LOG" not in caplog.text


def test_custom_forward_header_sends_the_raw_key() -> None:
    capture = Capture()
    harness = _harness(capture, header="X-Grok-Key")
    assert harness.post(task_body()).status_code == 202
    headers = capture.requests[0].headers
    assert headers["x-grok-key"] == GROK_KEY
    assert headers.get("authorization") != f"Bearer {GROK_KEY}"
