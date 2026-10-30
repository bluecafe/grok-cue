"""Inbound task shape. There is no Cue task_id field."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_ISSUE_OR_PR = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(issues|pull)/\d+"
)
_LINK = re.compile(r"https?://[^\s]+")

WorkStatus = Literal["in_progress", "needs_input", "completed", "failed"]
_MAX_ISSUE_NUMBER = 2_000_000_000
_MAX_SEQUENCE = 2**63 - 1


class GithubRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    base_branch: str
    start_sha: str
    paths: list[str] | None = None
    issue_or_pr_url: str | None = None
    issue_or_pr_number: int | None = None

    @field_validator("repo")
    @classmethod
    def repo_shape(cls, value: str) -> str:
        if _REPO.fullmatch(value) is None:
            raise ValueError("repo")
        return value

    @field_validator("base_branch")
    @classmethod
    def branch_shape(cls, value: str) -> str:
        if not value or len(value) > 255 or any(char.isspace() for char in value):
            raise ValueError("base_branch")
        return value

    @field_validator("start_sha")
    @classmethod
    def sha_shape(cls, value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise ValueError("start_sha")
        return value.lower()

    @field_validator("issue_or_pr_url")
    @classmethod
    def url_shape(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if _ISSUE_OR_PR.fullmatch(value) is None:
            raise ValueError("issue_or_pr_url")
        repo = info.data.get("repo") if info.data else None
        if isinstance(repo, str) and not value.startswith(f"https://github.com/{repo}/"):
            raise ValueError("issue_or_pr_url")
        return value

    @field_validator("issue_or_pr_number", mode="before")
    @classmethod
    def number_type(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("issue_or_pr_number")
        if value < 1 or value > _MAX_ISSUE_NUMBER:
            raise ValueError("issue_or_pr_number")
        return value

    @field_validator("issue_or_pr_number")
    @classmethod
    def number_matches_url(cls, value: int | None, info: ValidationInfo) -> int | None:
        if value is None:
            return None
        url = info.data.get("issue_or_pr_url") if info.data else None
        if isinstance(url, str) and url.rsplit("/", 1)[-1] != str(value):
            raise ValueError("issue_or_pr_number")
        return value

    @field_validator("paths")
    @classmethod
    def path_shape(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > 50:
            raise ValueError("paths")
        for path in value:
            parts = path.split("/")
            if (
                not path
                or len(path) > 200
                or path.startswith("/")
                or "\\" in path
                or any(char in path for char in "\n\r")
                or any(part == ".." for part in parts)
            ):
                raise ValueError("paths")
        return value


class CueTaskRequest(BaseModel):
    """Accept-only payload. `handoff_id` is the external id; Cue `task_id` is not a field."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
    handoff_id: UUID
    attempt: int
    report_seq: int
    github_ref: GithubRef
    repo: str
    idempotency_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    status: WorkStatus
    summary: str = Field(min_length=1, max_length=4000)
    created_at: datetime
    links: list[str] = Field(min_length=0, max_length=20)
    next_owner: str = Field(min_length=1, max_length=200)
    reply_to: str | None = None
    requested_by: str | None = Field(default=None, max_length=200)

    @field_validator("attempt", "report_seq", mode="before")
    @classmethod
    def sequence_number(cls, value: object, info: ValidationInfo) -> object:
        name = info.field_name or "sequence"
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(name)
        if value < 1 or value > _MAX_SEQUENCE:
            raise ValueError(name)
        return value

    @field_validator("repo")
    @classmethod
    def repo_matches_ref(cls, value: str, info: ValidationInfo) -> str:
        if _REPO.fullmatch(value) is None:
            raise ValueError("repo")
        ref = info.data.get("github_ref") if info.data else None
        if isinstance(ref, GithubRef) and ref.repo != value:
            raise ValueError("repo")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def key_chars(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 or char.isspace() for char in value):
            raise ValueError("idempotency_key")
        return value

    @field_validator("title")
    @classmethod
    def title_chars(cls, value: str) -> str:
        if not value.strip() or any(char in value for char in "\r\n"):
            raise ValueError("title")
        return value

    @field_validator("summary")
    @classmethod
    def summary_chars(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary")
        return value

    @field_validator("created_at")
    @classmethod
    def created_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("created_at")
        return value

    @field_validator("links")
    @classmethod
    def link_shape(cls, value: list[str]) -> list[str]:
        if len(value) > 20:
            raise ValueError("links")
        for link in value:
            if len(link) > 500 or _LINK.fullmatch(link) is None:
                raise ValueError("links")
        return value

    @field_validator("next_owner")
    @classmethod
    def next_owner_chars(cls, value: str) -> str:
        if not value.strip() or any(char in value for char in "\r\n"):
            raise ValueError("next_owner")
        return value

    @field_validator("requested_by")
    @classmethod
    def requested_by_chars(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or any(char in value for char in "\r\n"):
            raise ValueError("requested_by")
        return value
