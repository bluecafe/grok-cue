"""Drop configured secrets from log lines."""

from __future__ import annotations

import logging


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        redacted = rendered
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "[redacted]")
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True
