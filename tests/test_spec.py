"""docs/DESIGN*.md is the single spec. Model fields must match it exactly."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.forward import FORWARD_FIELDS
from app.main import MAX_BODY_BYTES
from app.models import CueTaskRequest, GithubRef
from tests.conftest import Harness
from tests.support import task_body

DOCS = Path(__file__).resolve().parent.parent / "docs"


def _spec_lists(name: str) -> tuple[set[str], set[str], tuple[str, ...]]:
    text = (DOCS / name).read_text(encoding="utf-8")
    section = re.search(r"^## (?:스펙|Spec)\n(.*?)(?=^## )", text, re.S | re.M)
    assert section, f"{name}: spec section missing"
    lines = section.group(1).splitlines()
    request = next(line for line in lines if re.match(r"\s+- (요청 필드|Request fields):", line))
    forward = next(line for line in lines if re.match(r"\s+- (전달 필드|Forwarded fields):", line))
    ref = next(line for line in lines if "`github_ref` " in line and line.strip().startswith("- `github_ref`"))
    grab = lambda line: set(re.findall(r"`([a-z_]+)`", line.split(":", 1)[1]))
    order = tuple(re.findall(r"`([a-z_]+)`", forward.split(":", 1)[1]))
    return grab(request), grab(ref), order


@pytest.mark.parametrize("doc", ["DESIGN.md", "DESIGN.en.md"])
def test_model_matches_spec(doc: str) -> None:
    request, ref, forward = _spec_lists(doc)
    model = set(CueTaskRequest.model_fields)
    assert model - request == set(), "in code but not in spec"
    assert request - model == set(), "in spec but not in code"
    assert set(GithubRef.model_fields) == ref
    assert forward == FORWARD_FIELDS
    assert set(forward) <= request


def test_spec_body_limit_value() -> None:
    assert MAX_BODY_BYTES == 65536
    for doc in ("DESIGN.md", "DESIGN.en.md"):
        assert "65536" in (DOCS / doc).read_text(encoding="utf-8")


def test_unknown_top_level_field_is_400(harness: Harness) -> None:
    payload = json.loads(task_body())
    payload["not_in_spec"] = "x"
    response = harness.post(json.dumps(payload).encode("utf-8"))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "not_in_spec" in response.json()["fields"]
