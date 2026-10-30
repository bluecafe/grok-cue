from __future__ import annotations

from tests.conftest import Harness
from tests.support import task_body


def test_omitted_reply_to_uses_default(harness: Harness) -> None:
    response = harness.post(task_body())
    assert response.status_code == 202
    assert harness.notifier.events == [
        (
            response.json()["handoff_id"],
            "example/repo",
            "cue@example.com",
            2,
        )
    ]


def test_blank_reply_to_uses_default(harness: Harness) -> None:
    response = harness.post(task_body(reply_to="   "))
    assert response.status_code == 202
    assert harness.notifier.events[0][2] == "cue@example.com"


def test_allowlisted_address_is_case_insensitive(harness: Harness) -> None:
    response = harness.post(task_body(reply_to="Cue@Example.Com"))
    assert response.status_code == 202
    assert harness.notifier.events[0][2] == "cue@example.com"


def test_reply_to_outside_allowlist_is_rejected(harness: Harness) -> None:
    response = harness.post(task_body(reply_to="other@example.com"))
    assert response.status_code == 400
    assert response.json() == {"error": "reply_to_not_allowed"}
    assert "allowed" not in response.json()
    assert "other@example.com" not in response.text
    assert "cue@example.com" not in response.text
    assert harness.notifier.events == []


def test_mailto_and_multiple_recipients_are_rejected(harness: Harness) -> None:
    mailto = harness.post(task_body(reply_to="mailto:cue@example.com"))
    multi = harness.post(
        task_body(
            idempotency_key="idem-multi",
            reply_to="cue@example.com, other@example.com",
        ),
        timestamp=int(harness.now) + 1,
    )
    assert mailto.status_code == 400
    assert mailto.json()["error"] == "reply_to_not_allowed"
    assert multi.status_code == 400
    assert multi.json()["error"] == "reply_to_not_allowed"
    assert harness.notifier.events == []


def test_rejected_reply_to_does_not_consume_idempotency_key(harness: Harness) -> None:
    rejected = harness.post(task_body(reply_to="other@example.com"))
    accepted = harness.post(task_body(), timestamp=int(harness.now) + 1)
    assert rejected.status_code == 400
    assert accepted.status_code == 202
    assert len(harness.notifier.events) == 1


def test_empty_allowlist_rejects_every_reply_to(harness_factory) -> None:
    harness = harness_factory(reply_to_allowlist="", reply_to_default=None)
    omitted = harness.post(task_body())
    explicit = harness.post(
        task_body(reply_to="cue@example.com", idempotency_key="idem-empty"),
        timestamp=int(harness.now) + 1,
    )
    assert omitted.status_code == 400
    assert omitted.json() == {"error": "reply_to_not_allowed"}
    assert explicit.status_code == 400
    assert explicit.json() == {"error": "reply_to_not_allowed"}
    assert "cue@example.com" not in omitted.text
    assert "cue@example.com" not in explicit.text
    assert harness.notifier.events == []


def test_default_must_be_on_a_custom_allowlist(harness_factory) -> None:
    harness = harness_factory(reply_to_allowlist="ops@example.com")
    omitted = harness.post(task_body())
    allowed = harness.post(
        task_body(reply_to="ops@example.com"),
        timestamp=int(harness.now) + 1,
    )
    assert omitted.status_code == 400
    assert omitted.json() == {"error": "reply_to_not_allowed"}
    assert "ops@example.com" not in omitted.text
    assert allowed.status_code == 202
    assert harness.notifier.events[0][2] == "ops@example.com"
