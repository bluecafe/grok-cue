from __future__ import annotations

import json

import pytest

from tests.conftest import Harness
from tests.support import REPO, github_ref, task_body


def test_task_id_field_is_rejected(harness: Harness) -> None:
    top = json.loads(task_body())
    top["task_id"] = "cue-internal"
    nested = json.loads(task_body(idempotency_key="idem-nested"))
    nested["github_ref"]["task_id"] = "cue-internal"
    first = harness.post(json.dumps(top).encode("utf-8"))
    second = harness.post(
        json.dumps(nested).encode("utf-8"),
        timestamp=int(harness.now) + 1,
    )
    assert first.status_code == 400
    assert first.json() == {"error": "task_id_not_allowed"}
    assert second.status_code == 400
    assert second.json() == {"error": "task_id_not_allowed"}
    assert harness.notifier.events == []


def test_invalid_request_does_not_echo_the_summary(harness: Harness) -> None:
    body = task_body(
        summary="SUMMARY_SHOULD_NOT_LEAK",
        github_ref=github_ref(start_sha="not-a-sha"),
    )
    response = harness.post(body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "github_ref.start_sha" in response.json()["fields"]
    assert "SUMMARY_SHOULD_NOT_LEAK" not in response.text
    assert harness.notifier.events == []


def test_issue_url_must_match_repo(harness: Harness) -> None:
    body = task_body(
        github_ref=github_ref(
            issue_or_pr_url="https://github.com/other/repo/issues/9",
        )
    )
    response = harness.post(body)
    assert response.status_code == 400
    assert response.json()["fields"] == ["github_ref.issue_or_pr_url"]


def test_malformed_json_is_400(harness: Harness) -> None:
    response = harness.post(b"{")
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request", "fields": ["body"]}


@pytest.mark.parametrize("status", ["in_progress", "needs_input", "completed", "failed"])
def test_work_status_is_received_not_executed(harness: Harness, status: str) -> None:
    response = harness.post(task_body(status=status))
    assert response.status_code == 202
    assert response.json()["status"] == "received"
    assert response.json()["executed"] is False


@pytest.mark.parametrize("status", ["request", "follow_up", "done"])
def test_old_or_unknown_status_is_rejected(harness: Harness, status: str) -> None:
    response = harness.post(task_body(status=status))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert response.json()["fields"] == ["status"]


def test_top_level_repo_must_match_github_ref(harness: Harness) -> None:
    response = harness.post(task_body(repo="other/repo"))
    assert response.status_code == 400
    assert response.json()["fields"] == ["repo"]


def test_issue_url_and_number_are_optional(harness: Harness) -> None:
    ref = github_ref()
    ref.pop("issue_or_pr_url")
    ref.pop("issue_or_pr_number")
    response = harness.post(task_body(github_ref=ref))
    assert response.status_code == 202
    assert response.json()["executed"] is False


def test_issue_number_alone_is_accepted(harness: Harness) -> None:
    ref = github_ref()
    ref.pop("issue_or_pr_url")
    response = harness.post(task_body(github_ref=ref))
    assert response.status_code == 202


def test_issue_url_alone_is_accepted(harness: Harness) -> None:
    ref = github_ref()
    ref.pop("issue_or_pr_number")
    response = harness.post(task_body(github_ref=ref))
    assert response.status_code == 202


def test_issue_number_must_match_url(harness: Harness) -> None:
    response = harness.post(task_body(github_ref=github_ref(issue_or_pr_number=2)))
    assert response.status_code == 400
    assert response.json()["fields"] == ["github_ref.issue_or_pr_number"]


def test_links_are_required_and_empty_list_is_allowed(harness: Harness) -> None:
    missing = json.loads(task_body())
    missing.pop("links")
    denied = harness.post(json.dumps(missing, separators=(",", ":")).encode("utf-8"))
    assert denied.status_code == 400
    assert denied.json()["error"] == "invalid_request"
    assert "links" in denied.json()["fields"]

    empty = harness.post(
        task_body(links=[], idempotency_key="idem-empty-links"),
        timestamp=int(harness.now) + 1,
    )
    assert empty.status_code == 202
    linked = harness.post(
        task_body(
            links=[f"https://github.com/{REPO}/pull/1"],
            idempotency_key="idem-one-link",
        ),
        timestamp=int(harness.now) + 2,
    )
    assert linked.status_code == 202


def test_next_owner_is_required(harness: Harness) -> None:
    missing = json.loads(task_body())
    missing.pop("next_owner")
    denied = harness.post(json.dumps(missing, separators=(",", ":")).encode("utf-8"))
    assert denied.status_code == 400
    assert "next_owner" in denied.json()["fields"]
    blank = harness.post(task_body(next_owner="  "), timestamp=int(harness.now) + 1)
    assert blank.status_code == 400
    assert blank.json()["fields"] == ["next_owner"]


def test_diff_source_and_patch_fields_are_rejected(harness: Harness) -> None:
    for offset, name in enumerate(("diff", "source", "patch"), start=1):
        payload = json.loads(task_body())
        payload[name] = "not-a-field"
        response = harness.post(
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            timestamp=int(harness.now) + offset,
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"
        assert name in response.json()["fields"]
