from __future__ import annotations

import tomllib
from pathlib import Path

from app import __version__
from tests.conftest import Harness

ROOT = Path(__file__).resolve().parent.parent


def test_healthz_is_plain_ok(harness: Harness) -> None:
    response = harness.http.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"
    assert response.headers["content-type"].startswith("text/plain")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert harness.http.app.version == version
    assert version not in response.text


def test_version_sync(harness: Harness) -> None:
    version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = pyproject["project"]["version"]
    assert version_file == package_version == __version__ == harness.http.app.version
