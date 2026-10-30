"""Process settings. Secrets come from the environment, never from the repo."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.reply_to import is_exact_email


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Names that are not fields, including PUBLIC_BASE_URL, are ignored.
        extra="ignore",
    )

    cue_bridge_token: str
    cue_bridge_hmac_key: str
    cue_bridge_token_secondary: str | None = None
    cue_bridge_hmac_key_secondary: str | None = None
    reply_to_allowlist: str = ""
    reply_to_default: str | None = None
    grok_webhook_url: str | None = None
    grok_webhook_key: str | None = None
    grok_webhook_key_header: str | None = None
    # Empty means process memory only. A path is chosen by the operator; there is no built-in location.
    cue_state_db: str | None = None

    @field_validator("cue_bridge_token", "cue_bridge_hmac_key")
    @classmethod
    def required_secret(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("required")
        return value

    @field_validator(
        "cue_bridge_token_secondary",
        "cue_bridge_hmac_key_secondary",
        "grok_webhook_url",
        "grok_webhook_key",
        "grok_webhook_key_header",
        "cue_state_db",
        "reply_to_default",
        mode="before",
    )
    @classmethod
    def blank_secondary(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("reply_to_allowlist")
    @classmethod
    def normalize_allowlist(cls, value: str) -> str:
        if not value or not value.strip():
            return ""
        seen: list[str] = []
        for part in value.split(","):
            item = part.strip().lower()
            if not item:
                continue
            if not is_exact_email(item):
                raise ValueError("REPLY_TO_ALLOWLIST entry is not a single email")
            if item not in seen:
                seen.append(item)
        return ",".join(seen)

    @field_validator("reply_to_default")
    @classmethod
    def normalize_default(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not is_exact_email(normalized):
            raise ValueError("REPLY_TO_DEFAULT")
        return normalized

    def bearer_tokens(self) -> list[str]:
        tokens = [self.cue_bridge_token]
        if self.cue_bridge_token_secondary:
            tokens.append(self.cue_bridge_token_secondary)
        return tokens

    def hmac_keys(self) -> list[str]:
        keys = [self.cue_bridge_hmac_key]
        if self.cue_bridge_hmac_key_secondary:
            keys.append(self.cue_bridge_hmac_key_secondary)
        return keys

    def reply_recipients(self) -> list[str]:
        if not self.reply_to_allowlist:
            return []
        return self.reply_to_allowlist.split(",")

    def forwarding_enabled(self) -> bool:
        return bool(self.grok_webhook_url and self.grok_webhook_key)

    def secret_values(self) -> list[str]:
        values = [self.cue_bridge_token, self.cue_bridge_hmac_key]
        if self.cue_bridge_token_secondary:
            values.append(self.cue_bridge_token_secondary)
        if self.cue_bridge_hmac_key_secondary:
            values.append(self.cue_bridge_hmac_key_secondary)
        if self.grok_webhook_key:
            values.append(self.grok_webhook_key)
        return values
