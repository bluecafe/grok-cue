from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.settings import Settings


def test_public_base_url_does_not_change_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUE_BRIDGE_TOKEN", "token-a")
    monkeypatch.setenv("CUE_BRIDGE_HMAC_KEY", "hmac-a")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    plain = Settings(_env_file=None)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://hook.example.com")
    with_url = Settings(_env_file=None)
    assert "public_base_url" not in Settings.model_fields
    assert plain.model_dump() == with_url.model_dump()
    assert with_url.forwarding_enabled() is False


def test_summary_retention_defaults_to_thirty_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUE_BRIDGE_TOKEN", "token-a")
    monkeypatch.setenv("CUE_BRIDGE_HMAC_KEY", "hmac-a")
    monkeypatch.delenv("CUE_SUMMARY_RETENTION_DAYS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.cue_summary_retention_days == 30
    assert settings.summary_retention_seconds() == 30 * 86400
    monkeypatch.setenv("CUE_SUMMARY_RETENTION_DAYS", "  ")
    blank = Settings(_env_file=None)
    assert blank.cue_summary_retention_days == 30


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "Infinity", "-inf", "0", "-1", "0.0"])
def test_summary_retention_rejects_non_finite_and_non_positive(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("CUE_BRIDGE_TOKEN", "token-a")
    monkeypatch.setenv("CUE_BRIDGE_HMAC_KEY", "hmac-a")
    monkeypatch.setenv("CUE_SUMMARY_RETENTION_DAYS", raw)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    with pytest.raises(ValidationError):
        create_app()
