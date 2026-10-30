"""Docs routes stay closed, and every response carries the security headers."""

from __future__ import annotations

import pytest
from fastapi.responses import Response

from app.main import _CONTENT_SECURITY_POLICY, apply_security_headers
from tests.conftest import Harness
from tests.support import task_body

_POLICY = "default-src 'none'; frame-ancestors 'none'"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_routes_are_404(harness: Harness, path: str) -> None:
    response = harness.http.get(path)
    assert response.status_code == 404
    assert "openapi" not in response.text.lower()


def test_security_headers_on_healthz_docs_and_rejection(harness: Harness) -> None:
    health = harness.http.get("/healthz")
    missing = harness.http.get("/docs")
    rejected = harness.post(task_body(reply_to="other@example.com"))
    assert health.status_code == 200
    assert missing.status_code == 404
    assert rejected.status_code == 400
    for response in (health, missing, rejected):
        assert response.headers["content-security-policy"] == _POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
    assert _CONTENT_SECURITY_POLICY == _POLICY


def test_existing_nosniff_value_is_left_in_place() -> None:
    response = Response(content="ok", media_type="text/plain")
    response.headers["X-Content-Type-Options"] = "already"
    apply_security_headers(response)
    assert response.headers["x-content-type-options"] == "already"
    assert response.headers["content-security-policy"] == _POLICY
