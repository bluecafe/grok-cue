from __future__ import annotations

from tests.conftest import Harness


def test_healthz_is_plain_ok(harness: Harness) -> None:
    response = harness.http.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"
    assert response.headers["content-type"].startswith("text/plain")
