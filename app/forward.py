"""Forward an accepted handoff to Grok. This is not execution or approval."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx2 as httpx

from app.models import CueTaskRequest
from app.settings import Settings

logger = logging.getLogger("grok_cue")

FORWARD_TIMEOUT_SECONDS = 5.0

# 7.1 names these HTTP statuses as retryable. P1 only classifies; it does not retry.
RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429})

ForwardClass = Literal["skipped", "delivered", "retryable", "dead"]

# Top-level keys sent to Grok. Nothing else from the Cue body is copied.
FORWARD_FIELDS = (
    "handoff_id",
    "repo",
    "github_ref",
    "status",
    "summary",
    "links",
    "next_owner",
    "idempotency_key",
)


def handoff_payload(model: CueTaskRequest) -> dict[str, Any]:
    """JSON object whose keys are exactly FORWARD_FIELDS."""
    payload = {
        "handoff_id": str(model.handoff_id),
        "repo": model.repo,
        "github_ref": model.github_ref.model_dump(mode="json", exclude_none=True),
        "status": model.status,
        "summary": model.summary,
        "links": list(model.links),
        "next_owner": model.next_owner,
        "idempotency_key": model.idempotency_key,
    }
    if tuple(payload) != FORWARD_FIELDS:
        raise RuntimeError("forward payload keys drifted")
    return payload


def classify_http_status(status: int) -> ForwardClass:
    """Map a Grok HTTP status to a forward class. Does not perform I/O."""
    if 200 <= status <= 299:
        return "delivered"
    if status in RETRYABLE_HTTP_STATUSES or 500 <= status <= 599:
        return "retryable"
    return "dead"


def forward_headers(key: str, header_name: str | None) -> dict[str, str]:
    """Default is Bearer. A configured header name carries the raw key."""
    name = (header_name or "").strip()
    if not name:
        return {"Authorization": f"Bearer {key}"}
    return {name: key}


class GrokForwarder:
    """POST the handoff. Failures are logged and swallowed."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = FORWARD_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self.timeout = timeout
        self._transport = transport

    def enabled(self) -> bool:
        return self._settings.forwarding_enabled()

    async def send(self, handoff_id: str, payload: dict[str, Any]) -> None:
        url = self._settings.grok_webhook_url
        key = self._settings.grok_webhook_key
        if not url or not key:
            logger.info("forward handoff_id=%s outcome=skipped class=skipped", handoff_id)
            return
        headers = forward_headers(key, self._settings.grok_webhook_key_header)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self._transport,
            ) as client:
                response = await asyncio.wait_for(
                    client.post(url, json=payload, headers=headers),
                    timeout=self.timeout,
                )
        except (TimeoutError, httpx.TimeoutException):
            logger.info(
                "forward handoff_id=%s outcome=timeout class=retryable",
                handoff_id,
            )
            return
        except httpx.TransportError:
            logger.info(
                "forward handoff_id=%s outcome=connection_error class=retryable",
                handoff_id,
            )
            return
        except Exception:
            logger.info("forward handoff_id=%s outcome=error class=dead", handoff_id)
            return
        outcome_class = classify_http_status(response.status_code)
        logger.info(
            "forward handoff_id=%s outcome=http status=%s class=%s",
            handoff_id,
            response.status_code,
            outcome_class,
        )
