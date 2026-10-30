# Env must be set before app.main builds the default app at import.
import os

os.environ.setdefault("CUE_BRIDGE_TOKEN", "test-token")
os.environ.setdefault("CUE_BRIDGE_HMAC_KEY", "test-hmac-key")
os.environ.setdefault("REPLY_TO_ALLOWLIST", "cue@example.com")
os.environ.setdefault("REPLY_TO_DEFAULT", "cue@example.com")

from collections.abc import Callable, Iterator

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.forward import GrokForwarder
from app.main import create_app
from app.notify import LogNotifier
from app.settings import Settings
from tests.support import FIXED_NOW, HMAC_KEY, TOKEN, sign, sign_v2


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, int]] = []
        self._log = LogNotifier()

    def accepted(self, handoff_id: str, repo: str, reply_to: str, signature_version: int) -> None:
        self.events.append((handoff_id, repo, reply_to, signature_version))
        self._log.accepted(handoff_id, repo, reply_to, signature_version)


class Harness:
    def __init__(
        self,
        http: TestClient,
        notifier: RecordingNotifier,
        settings: Settings,
        now: float,
    ) -> None:
        self.http = http
        self.notifier = notifier
        self.settings = settings
        self.now = now
        self.outbox = None
        self.forwarder = None
        self._entered = False

    def close(self) -> None:
        if self._entered:
            self.http.__exit__(None, None, None)
        else:
            self.http.close()

    def post(
        self,
        body: bytes,
        *,
        timestamp: int | None = None,
        token: str | None = None,
        hmac_key: str | None = None,
        signature: str | None = None,
        signature_version: str | None = None,
        omit_version: bool = False,
        sign_method: str = "POST",
        sign_path: str = "/v1/cue/tasks",
        content_type: str | None = "application/json",
        drop: tuple[str, ...] = (),
    ):
        ts_value = str(int(self.now) if timestamp is None else timestamp)
        key = self.settings.cue_bridge_hmac_key if hmac_key is None else hmac_key
        version_header = "2" if signature_version is None else signature_version
        if signature is not None:
            sig = signature
        elif version_header.strip() == "2":
            sig = sign_v2(key, sign_method, sign_path, ts_value, body)
        else:
            sig = sign(key, ts_value, body)
        headers = {
            "Authorization": f"Bearer {token if token is not None else self.settings.cue_bridge_token}",
            "X-Cue-Timestamp": ts_value,
            "X-Cue-Signature": sig,
        }
        if not omit_version:
            headers["X-Cue-Signature-Version"] = version_header
        if content_type is not None:
            headers["Content-Type"] = content_type
        for name in drop:
            headers.pop(name, None)
        return self.http.post("/v1/cue/tasks", content=body, headers=headers)

    def pump(self, at: float, jitter: float = 1.0, clock: Callable[[], float] | None = None) -> None:
        asyncio.run(self.outbox.pump(self.forwarder, at, jitter, clock=clock))


def build_harness(
    *,
    reply_to_allowlist: str = "cue@example.com",
    reply_to_default: str | None = "cue@example.com",
    token_secondary: str | None = None,
    hmac_secondary: str | None = None,
    grok_webhook_url: str | None = None,
    grok_webhook_key: str | None = None,
    grok_webhook_key_header: str | None = None,
    state_db: str | None = None,
    forwarder: GrokForwarder | None = None,
    now: float = FIXED_NOW,
    jitter=None,
    auto_pump: bool = True,
    clock: Callable[[], float] | None = None,
    poll_seconds: float = 5.0,
    lifespan: bool = False,
) -> Harness:
    settings = Settings(
        cue_bridge_token=TOKEN,
        cue_bridge_hmac_key=HMAC_KEY,
        cue_bridge_token_secondary=token_secondary,
        cue_bridge_hmac_key_secondary=hmac_secondary,
        reply_to_allowlist=reply_to_allowlist,
        reply_to_default=reply_to_default,
        grok_webhook_url=grok_webhook_url,
        grok_webhook_key=grok_webhook_key,
        grok_webhook_key_header=grok_webhook_key_header,
        cue_state_db=state_db,
        cue_outbox_poll_seconds=poll_seconds,
    )
    notifier = RecordingNotifier()
    app = create_app(
        settings,
        clock=clock or (lambda: now),
        notifier=notifier,
        forwarder=forwarder,
        jitter=jitter,
        auto_pump=auto_pump,
    )
    http = TestClient(app)
    built = Harness(http, notifier, settings, now)
    built.outbox = app.state.bridge.outbox
    built.forwarder = app.state.bridge.forwarder
    if lifespan:
        http.__enter__()
        built._entered = True
    return built


@pytest.fixture
def harness() -> Iterator[Harness]:
    built = build_harness()
    try:
        yield built
    finally:
        built.http.close()


@pytest.fixture
def harness_factory() -> Iterator[Callable[..., Harness]]:
    opened: list[Harness] = []

    def factory(
        *,
        reply_to_allowlist: str = "cue@example.com",
        reply_to_default: str | None = "cue@example.com",
        token_secondary: str | None = None,
        hmac_secondary: str | None = None,
        now: float = FIXED_NOW,
    ) -> Harness:
        built = build_harness(
            reply_to_allowlist=reply_to_allowlist,
            reply_to_default=reply_to_default,
            token_secondary=token_secondary,
            hmac_secondary=hmac_secondary,
            now=now,
        )
        opened.append(built)
        return built

    try:
        yield factory
    finally:
        for built in opened:
            built.http.close()
