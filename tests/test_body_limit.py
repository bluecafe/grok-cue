"""The 64 KiB limit is enforced before authentication."""

from __future__ import annotations

from collections.abc import Iterator

from app.main import MAX_BODY_BYTES
from tests.conftest import Harness
from tests.support import task_body


def _padded(size: int) -> bytes:
    body = task_body()
    return body + b" " * (size - len(body))


def test_oversized_with_content_length_rejected_without_auth(harness: Harness) -> None:
    response = harness.http.post(
        "/v1/cue/tasks",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request", "fields": ["body"]}


def test_oversized_chunked_without_content_length_rejected_without_auth(
    harness: Harness,
) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(10):
            yield b"x" * 16384

    response = harness.http.post(
        "/v1/cue/tasks",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request", "fields": ["body"]}


def test_invalid_content_length_rejected(harness: Harness) -> None:
    response = harness.http.post(
        "/v1/cue/tasks",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "abc"},
    )
    assert response.status_code == 400


def test_exactly_limit_is_accepted(harness: Harness) -> None:
    body = _padded(MAX_BODY_BYTES)
    assert len(body) == MAX_BODY_BYTES
    response = harness.post(body)
    assert response.status_code == 202


def test_one_byte_over_limit_with_valid_auth_is_rejected(harness: Harness) -> None:
    response = harness.post(_padded(MAX_BODY_BYTES + 1))
    assert response.status_code == 400
    assert response.json()["fields"] == ["body"]
    assert harness.notifier.events == []
