"""Single acceptance hook. This skeleton logs and does not send mail."""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("grok_cue")


class Notifier(Protocol):
    def accepted(self, handoff_id: str, repo: str, reply_to: str) -> None:
        """Record a first acceptance. Must not run, merge, deploy, or pay."""


class LogNotifier:
    def accepted(self, handoff_id: str, repo: str, reply_to: str) -> None:
        # reply_to is an allowlisted address. The body, token, and HMAC are not logged.
        logger.info(
            "accepted handoff_id=%s repo=%s reply_to=%s executed=false notify=skipped",
            handoff_id,
            repo,
            reply_to,
        )
