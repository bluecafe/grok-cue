from __future__ import annotations

import pytest

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
