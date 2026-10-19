"""`POST /api/v1/tickets/form` (public) and `POST /api/v1/tickets/email` (HMAC-signed).

The form answers every accepted-looking request the same way (202, no ticket id), whether it made
a ticket, repeated one, or was a bot caught by the honeypot, so the response tells a caller
nothing about what the server did. The email endpoint is authenticated, so it says what happened.
"""

import hashlib
import hmac
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from services.api.auth.ratelimit import SlidingWindowLimiter, client_ip
from services.api.clock import Clock
from services.api.intake.cleaning import EmptyMessageError
from services.api.intake.payloads import AttachmentIn, EmailPayload, FormPayload
from services.api.intake.service import IntakeService
from services.api.models.common import StrictModel
from services.api.models.enums import Channel
from services.api.models.ticket import Customer, TicketCreateRequest
from services.api.settings import IntakeSettings

log = logging.getLogger("replydesk.intake")

router = APIRouter(prefix="/api/v1/tickets", tags=["intake"])

SIGNATURE_HEADER = "x-signature"
SIGNATURE_PREFIX = "sha256="
NO_STORE = {"Cache-Control": "no-store"}
Model = TypeVar("Model", bound=StrictModel)


@dataclass
class IntakeRuntime:
    service: IntakeService
    settings: IntakeSettings
    per_minute: SlidingWindowLimiter
    per_hour: SlidingWindowLimiter

    @classmethod
    def create(
        cls, service: IntakeService, settings: IntakeSettings, clock: Clock
    ) -> "IntakeRuntime":
        return cls(
            service,
            settings,
            SlidingWindowLimiter(settings.form_ip_max_per_minute, 60, clock),
            SlidingWindowLimiter(settings.form_ip_max_per_hour, 3600, clock),
        )


def get_intake(request: Request) -> IntakeRuntime:
    runtime: IntakeRuntime | None = getattr(request.app.state, "intake", None)
    if runtime is None:
        raise HTTPException(503, "ticket intake is not available")
    return runtime


Intake = Annotated[IntakeRuntime, Depends(get_intake)]


async def read_limited_body(request: Request, limit: int) -> bytes:
    """The request body, refusing (413) as soon as it is known to exceed `limit` bytes."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "request body too large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _require_json(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "send application/json")


def _invalid(errors: Sequence[Mapping[str, Any]]) -> HTTPException:
    return HTTPException(422, [{"loc": list(e["loc"]), "msg": e["msg"]} for e in errors])


def _parse(model: type[Model], raw: bytes) -> Model:
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        # include_input is off: the response must not echo the message or the sender's details.
        raise _invalid(
            exc.errors(include_input=False, include_url=False, include_context=False)
        ) from None


def _check_limits(
    settings: IntakeSettings, text: str, max_chars: int, attachments: list[AttachmentIn]
) -> None:
    problems: list[dict[str, Any]] = []
    if len(text) > max_chars:
        problems.append({"loc": ["body"], "msg": f"must be at most {max_chars} characters"})
    if len(attachments) > settings.attachment_max_count:
        problems.append(
            {"loc": ["attachments"], "msg": f"at most {settings.attachment_max_count} attachments"}
        )
    if any(item.size_bytes > settings.attachment_max_bytes for item in attachments):
        problems.append({"loc": ["attachments"], "msg": "an attachment is too large"})
    if problems:
        raise _invalid(problems)


async def _ingest(runtime: IntakeRuntime, ticket: TicketCreateRequest) -> Any:
    try:
        result = await runtime.service.ingest(ticket)
    except EmptyMessageError:
        raise HTTPException(422, "message is empty") from None
    log.info(
        "ticket received ticket_id=%s channel=%s created=%s",
        result.ticket_id,
        ticket.channel.value,
        result.created,
    )
    return result


@router.post("/form", status_code=202)
async def submit_form(request: Request, runtime: Intake) -> JSONResponse:
    settings = runtime.settings
    ip = client_ip(
        request.headers,
        request.client.host if request.client else None,
        settings.trusted_proxy_hops,
    )
    wait = runtime.per_minute.hit(ip) or runtime.per_hour.hit(ip)
    if wait:
        return JSONResponse(
            {"detail": "too many requests"}, status_code=429, headers={"Retry-After": str(wait)}
        )
    _require_json(request)
    form = _parse(FormPayload, await read_limited_body(request, settings.form_max_body_bytes))
    received = JSONResponse({"status": "received"}, status_code=202, headers=NO_STORE)
    if form.website:
        return received  # a bot: look successful, store nothing
    _check_limits(settings, form.message, settings.form_message_max_chars, form.attachments)
    submission = form.submission_id or uuid4()
    await _ingest(
        runtime,
        TicketCreateRequest(
            external_message_id=f"form-{submission}",
            channel=Channel.FORM,
            customer=Customer(email=form.email, name=form.name),
            subject=form.subject,
            body=form.message,
            order_id_hint=form.order_id,
            attachments=list(form.attachments),
        ),
    )
    return received


def _signature_matches(secret: bytes, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len(SIGNATURE_PREFIX) :].strip().lower(), expected)


@router.post("/email")
async def submit_email(request: Request, runtime: Intake) -> JSONResponse:
    settings = runtime.settings
    if settings.hmac_secret is None:
        raise HTTPException(503, "email intake is not configured")
    body = await read_limited_body(request, settings.email_max_body_bytes)
    secret = settings.hmac_secret.get_secret_value().encode()
    if not _signature_matches(secret, body, request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(401, "invalid signature")
    _require_json(request)
    email = _parse(EmailPayload, body)
    _check_limits(settings, email.body, settings.email_body_max_chars, email.attachments)
    result = await _ingest(
        runtime,
        TicketCreateRequest(
            external_message_id=email.message_id,
            channel=Channel.EMAIL,
            received_at=email.received_at,
            customer=email.customer,
            subject=email.subject,
            body=email.body,
            in_reply_to=email.in_reply_to,
            order_id_hint=email.order_id_hint,
            attachments=list(email.attachments),
        ),
    )
    return JSONResponse(
        {
            "ticket_id": str(result.ticket_id),
            "thread_id": str(result.thread_id),
            "created": result.created,
        },
        status_code=201 if result.created else 200,
        headers=NO_STORE,
    )
