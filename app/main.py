"""Accept-only HTTP skeleton. Starting this process does not deploy the bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.auth import (
    SKEW_SECONDS,
    ReplayCache,
    bearer_matches,
    parse_bearer,
    parse_unix_timestamp,
    parse_signature_version,
    signature_matches,
)
from app.forward import GrokForwarder, handoff_payload
from app.models import CueTaskRequest
from app.notify import LogNotifier, Notifier
from app.outbox import MemoryOutbox, jitter_factor, run_outbox_worker
from app.persistence import ReplaySeen, StateDB, StorageUnavailable
from app.redact import SecretRedactingFilter
from app.reply_to import ReplyToNotAllowed, resolve_reply_to
from app.settings import Settings
from app.store import IdempotencyConflict, IdempotencyStore, SequenceConflict

logger = logging.getLogger("grok_cue")


def read_version() -> str:
    """Version string for the FastAPI app. /healthz does not include it."""
    path = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text or "0.0.0"


MAX_BODY_BYTES = 64 * 1024
_NO_STORE = {"Cache-Control": "no-store"}


@dataclass
class AppState:
    settings: Settings
    clock: Callable[[], float]
    store: IdempotencyStore | StateDB
    replay: ReplayCache | StateDB
    notifier: Notifier
    forwarder: GrokForwarder
    outbox: MemoryOutbox | StateDB
    durable: StateDB | None = None
    jitter: Callable[[], float] = jitter_factor


def _install_redaction(settings: Settings) -> None:
    logger.setLevel(logging.INFO)
    logger.filters = [
        item for item in logger.filters if not isinstance(item, SecretRedactingFilter)
    ]
    logger.addFilter(SecretRedactingFilter(settings.secret_values()))
    # Uvicorn does not show this logger's INFO records unless the logger has a handler.
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)


def json_error(status: int, code: str, **extra: object) -> JSONResponse:
    payload: dict[str, object] = {"error": code}
    payload.update(extra)
    return JSONResponse(status_code=status, content=payload, headers=_NO_STORE)


def unauthorized() -> JSONResponse:
    return json_error(401, "unauthorized")


def storage_unavailable() -> JSONResponse:
    logger.info("rejected error=storage_unavailable")
    return json_error(503, "storage_unavailable")


def invalid(fields: list[str]) -> JSONResponse:
    logger.info("rejected error=invalid_request")
    return json_error(400, "invalid_request", fields=fields)


def fields_from(exc: ValidationError) -> list[str]:
    fields: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        name = loc or "body"
        if name not in fields:
            fields.append(name)
    return fields


async def read_limited_body(request: Request) -> bytes | None:
    """Read at most MAX_BODY_BYTES; return None as soon as the body is larger."""
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > MAX_BODY_BYTES:
            return None
    return bytes(buffer)


def json_has_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        if key in value:
            return True
        return any(json_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(json_has_key(item, key) for item in value)
    return False


@asynccontextmanager
async def _outbox_lifespan(app: FastAPI):
    """One in-process worker. It pumps immediately, then on the poll interval."""
    stop = asyncio.Event()
    task = asyncio.create_task(run_outbox_worker(app.state.bridge, stop))
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], float] | None = None,
    store: IdempotencyStore | None = None,
    replay: ReplayCache | None = None,
    notifier: Notifier | None = None,
    forwarder: GrokForwarder | None = None,
    jitter: Callable[[], float] | None = None,
    auto_pump: bool = True,
) -> FastAPI:
    resolved = settings if settings is not None else Settings()
    _install_redaction(resolved)
    durable: StateDB | None = None
    memory_outbox = MemoryOutbox()
    if store is None and replay is None and resolved.cue_state_db:
        durable = StateDB(resolved.cue_state_db)
        store = durable
        replay = durable
    state = AppState(
        settings=resolved,
        clock=clock or time.time,
        store=store or IdempotencyStore(),
        replay=replay or ReplayCache(),
        notifier=notifier or LogNotifier(),
        forwarder=forwarder or GrokForwarder(resolved),
        outbox=durable if durable is not None else memory_outbox,
        durable=durable,
        jitter=jitter or jitter_factor,
    )
    state.auto_pump = auto_pump
    app = FastAPI(title="grok-cue", version=read_version(), debug=False, lifespan=_outbox_lifespan)
    app.state.bridge = state

    @app.get("/healthz")
    def healthz() -> Response:
        return Response(content="ok", media_type="text/plain", status_code=200)

    @app.post("/v1/cue/tasks")
    async def create_task(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        # Accept only. Chat approval starts work later; this handler does not
        # run, merge, deploy, pay, or send mail.
        bridge: AppState = request.app.state.bridge
        # Size is checked before auth so unauthenticated callers cannot make
        # the process buffer large bodies.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > MAX_BODY_BYTES:
                    return invalid(["body"])
            except ValueError:
                return invalid(["body"])
        raw = await read_limited_body(request)
        if raw is None:
            return invalid(["body"])
        now = bridge.clock()
        header_ts = request.headers.get("x-cue-timestamp")
        header_sig = request.headers.get("x-cue-signature")
        bearer = parse_bearer(request.headers.get("authorization"))
        if header_ts is None or not header_ts.strip():
            logger.info("auth failed reason=missing_timestamp")
            return unauthorized()
        ts = parse_unix_timestamp(header_ts)
        if ts is None:
            logger.info("auth failed reason=malformed_timestamp")
            return unauthorized()
        if abs(now - float(ts)) > SKEW_SECONDS:
            logger.info("auth failed reason=timestamp_out_of_window")
            return unauthorized()

        timestamp = header_ts.strip()
        reasons: list[str] = []
        if bearer is None:
            reasons.append("missing_bearer")
        elif not bearer_matches(bearer, bridge.settings.bearer_tokens()):
            reasons.append("bearer_rejected")
        signature = (header_sig or "").strip()
        sig_version = parse_signature_version(request.headers.get("x-cue-signature-version"))
        if sig_version is None:
            reasons.append("unknown_signature_version")
        elif not signature:
            reasons.append("missing_signature")
        elif not signature_matches(
            header_sig,
            bridge.settings.hmac_keys(),
            timestamp,
            raw,
            version=sig_version,
            method=request.method,
            path=request.url.path,
        ):
            reasons.append("signature_rejected")
        if reasons:
            # Reason codes only. Never the token, key, signature, or body.
            logger.info("auth failed reason=%s", ",".join(reasons))
            return unauthorized()

        # A fully authenticated signature is spent. Retries must re-sign.
        # Durable mode records that spend with the acceptance, in one commit,
        # or on its own when the request is rejected after authentication.
        signature_key = signature.lower()
        if bridge.durable is None:
            if bridge.replay.seen_or_remember(signature_key, ts, now):
                logger.info("auth failed reason=replay")
                return unauthorized()
        else:
            try:
                if bridge.durable.seen(signature_key, now):
                    logger.info("auth failed reason=replay")
                    return unauthorized()
            except StorageUnavailable:
                return storage_unavailable()

        def spend_replay() -> JSONResponse | None:
            if bridge.durable is None:
                return None
            try:
                bridge.durable.remember(signature_key, ts, now)
            except StorageUnavailable:
                return storage_unavailable()
            return None

        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            if (blocked := spend_replay()) is not None:
                return blocked
            return invalid(["content-type"])
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            if (blocked := spend_replay()) is not None:
                return blocked
            return invalid(["body"])
        if not isinstance(data, dict):
            if (blocked := spend_replay()) is not None:
                return blocked
            return invalid(["body"])
        if json_has_key(data, "task_id"):
            logger.info("rejected error=task_id_not_allowed")
            if (blocked := spend_replay()) is not None:
                return blocked
            return json_error(400, "task_id_not_allowed")

        recipients = bridge.settings.reply_recipients()
        default_reply = bridge.settings.reply_to_default
        if "reply_to" in data:
            try:
                resolve_reply_to(data["reply_to"], recipients, default_reply)
            except ReplyToNotAllowed:
                logger.info("rejected error=reply_to_not_allowed")
                if (blocked := spend_replay()) is not None:
                    return blocked
                return json_error(400, "reply_to_not_allowed", allowed=recipients)

        try:
            model = CueTaskRequest.model_validate(data)
        except ValidationError as exc:
            if (blocked := spend_replay()) is not None:
                return blocked
            return invalid(fields_from(exc))

        try:
            reply_to = resolve_reply_to(model.reply_to, recipients, default_reply)
        except ReplyToNotAllowed:
            logger.info("rejected error=reply_to_not_allowed")
            if (blocked := spend_replay()) is not None:
                return blocked
            return json_error(400, "reply_to_not_allowed", allowed=recipients)

        response_body: dict[str, object] = {
            "handoff_id": str(model.handoff_id),
            "status": "received",
            "executed": False,
        }
        forward_payload = handoff_payload(model) if bridge.forwarder.enabled() else None
        try:
            if bridge.durable is not None:
                result = bridge.durable.accept(
                    signature_key,
                    ts,
                    now,
                    model.idempotency_key,
                    raw,
                    response_body,
                    handoff_id=str(model.handoff_id),
                    attempt=model.attempt,
                    report_seq=model.report_seq,
                    signature_version=sig_version,
                    forward_payload=forward_payload,
                    forwarding=forward_payload is not None,
                )
            else:
                result = bridge.store.register(
                    model.idempotency_key,
                    raw,
                    response_body,
                    handoff_id=str(model.handoff_id),
                    attempt=model.attempt,
                    report_seq=model.report_seq,
                )
                if result.created and result.enqueue:
                    bridge.outbox.enqueue(
                        model.idempotency_key,
                        str(model.handoff_id),
                        forward_payload,
                        enabled=forward_payload is not None,
                        now=now,
                    )
        except ReplaySeen:
            logger.info("auth failed reason=replay")
            return unauthorized()
        except StorageUnavailable:
            return storage_unavailable()
        except IdempotencyConflict:
            logger.info("rejected error=idempotency_conflict")
            return json_error(409, "idempotency_conflict")
        except SequenceConflict:
            logger.info("rejected error=sequence_conflict")
            return json_error(409, "sequence_conflict")

        if result.created:
            bridge.notifier.accepted(
                handoff_id=str(result.response["handoff_id"]),
                repo=model.repo,
                reply_to=reply_to,
                signature_version=sig_version,
            )
            _forwarded, forward_state = bridge.outbox.view(model.idempotency_key)
            if forward_state == "queued" and bridge.auto_pump:
                background_tasks.add_task(
                    bridge.outbox.pump,
                    bridge.forwarder,
                    now,
                    bridge.jitter(),
                    clock=bridge.clock,
                )
        else:
            logger.info(
                "idempotent handoff_id=%s created=false notify=skipped forward=skipped",
                result.response["handoff_id"],
            )
        # `forwarded` is true only after Grok 2xx. A duplicate does not send again.
        delivered, forward_state = bridge.outbox.view(model.idempotency_key)
        outgoing = dict(result.response)
        outgoing["executed"] = False
        outgoing["forwarded"] = delivered
        outgoing["forward_state"] = forward_state
        return JSONResponse(status_code=202, content=outgoing, headers=_NO_STORE)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.info("rejected error=internal type=%s", type(exc).__name__)
        return json_error(500, "internal")

    return app


def _default_app() -> FastAPI:
    try:
        return create_app()
    except ValidationError:
        raise RuntimeError(
            "Bridge settings are invalid. Check CUE_BRIDGE_TOKEN, "
            "CUE_BRIDGE_HMAC_KEY, REPLY_TO_ALLOWLIST, and REPLY_TO_DEFAULT."
        ) from None


app = _default_app()
