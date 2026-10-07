"""Panel page "Verificaciones": the pending requests left by request_operator_verification
(units without owner email), for operators and admins.

- GET       /admin/verifications                   the pending ones, oldest first
- GET/POST  /admin/verifications/{id}/approve      choose the owner the phone is linked to
- POST      /admin/verifications/{id}/reject       the phone is not linked

Audited in bot_events (verification_approved, verification_rejected). The forms carry no CSRF
token, like the rest of the panel: the session cookie is SameSite=Strict (app.admin.auth).
"""

from typing import Any, ClassVar

from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin import formatting, labels
from app.admin.audit import log_admin_action
from app.admin.auth import admin_user
from app.admin.views import _in_thread
from app.bot.identity import (
    IdentityError,
    approve_verification_request,
    reject_verification_request,
)
from app.config import Settings
from app.db.models import (
    Person,
    PersonRole,
    Unit,
    UnitPerson,
    VerificationRequest,
    VerificationRequestStatus,
    WaContact,
    WaConversation,
)

STATUS_LABELS = labels.VERIFICATION_STATUS
REJECT_CONFIRMATION = "¿Rechazar esta verificación? El número no se asocia."


def problem_text(exc: IdentityError) -> str:
    """app.bot.identity's message, for the panel (without ids nor raw statuses)."""
    text = str(exc)
    if "ya resuelta" in text:
        return "Esta solicitud ya estaba resuelta."
    if "inexistente" in text:
        return "La solicitud no existe."
    return text[:1].upper() + text[1:] + "."


def _with_unit(stmt: Select) -> Select:
    return stmt.options(selectinload(VerificationRequest.unit).selectinload(Unit.building))


def unit_owners(session: Session, unit_id: int) -> list[Person]:
    return list(
        session.scalars(
            select(Person)
            .join(UnitPerson, UnitPerson.person_id == Person.id)
            .where(UnitPerson.unit_id == unit_id, UnitPerson.role == PersonRole.OWNER)
            .order_by(Person.full_name, Person.id)
        )
    )


def conversation_id(session: Session, phone_e164: str) -> int | None:
    """The WhatsApp conversation of that phone, if it wrote (one per contact)."""
    return session.scalar(
        select(WaConversation.id)
        .join(WaContact, WaContact.id == WaConversation.contact_id)
        .where(WaContact.phone_e164 == phone_e164)
    )


def approve(session: Session, request_id: int, person_id: int, user: str) -> None:
    request = approve_verification_request(session, request_id, person_id, resolved_by=user)
    log_admin_action(
        session,
        user,
        "verification_approved",
        phone_e164=request.phone_e164,
        request_id=request.id,
        unit_id=request.unit_id,
        person_id=person_id,
    )
    session.commit()


def reject(session: Session, request_id: int, user: str) -> None:
    request = reject_verification_request(session, request_id, resolved_by=user)
    log_admin_action(
        session,
        user,
        "verification_rejected",
        phone_e164=request.phone_e164,
        request_id=request.id,
        unit_id=request.unit_id,
    )
    session.commit()


class VerificationsView(BaseView):
    name = "Verificaciones"
    icon = "fa-solid fa-user-check"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default

    def _to_list(self, request: Request) -> RedirectResponse:
        return RedirectResponse(request.url_for("admin:view-verifications"), status_code=302)

    @expose("/verifications", methods=["GET"], identity="verifications")
    async def verifications_page(self, request: Request) -> Response:
        with self.session_maker() as session:
            pending = session.scalars(
                _with_unit(
                    select(VerificationRequest)
                    .where(VerificationRequest.status == VerificationRequestStatus.PENDING)
                    .order_by(VerificationRequest.created_at, VerificationRequest.id)
                )
            ).all()
            rows = [
                {
                    "id": v.id,
                    "building": formatting.building(v.unit.building.name),
                    "unit": v.unit.label,
                    "claimed_name": v.claimed_name,
                    "phone": formatting.phone(v.phone_e164),
                    "date": formatting.when(v.created_at, timezone=self.timezone),
                    "status": STATUS_LABELS[VerificationRequestStatus(v.status)],
                }
                for v in pending
            ]
        return await self.templates.TemplateResponse(
            request,
            "verifications.html",
            {"title": "Verificaciones", "rows": rows, "reject_confirmation": REJECT_CONFIRMATION},
        )

    @expose(
        "/verifications/{request_id:int}/reject", methods=["POST"], identity="verification-reject"
    )
    async def reject_one(self, request: Request) -> Response:
        with self.session_maker() as session:
            try:
                await _in_thread(
                    reject, session, request.path_params["request_id"], admin_user(request)
                )
            except IdentityError as exc:
                session.rollback()
                Flash.error(request, problem_text(exc))
            else:
                Flash.success(request, "Verificación rechazada: el número no se asoció.")
        return self._to_list(request)

    @expose(
        "/verifications/{request_id:int}/approve",
        methods=["GET", "POST"],
        identity="verification-approve",
    )
    async def approve_page(self, request: Request) -> Response:
        pk = request.path_params["request_id"]
        error = None
        with self.session_maker() as session:
            if request.method == "POST":
                form = await request.form()
                try:
                    person_id = int(str(form.get("person_id", "")))
                except ValueError:
                    error = "Elegí el propietario al que se asocia el teléfono."
                else:
                    try:
                        await _in_thread(approve, session, pk, person_id, admin_user(request))
                    except IdentityError as exc:
                        session.rollback()
                        error = problem_text(exc)
                    else:
                        Flash.success(request, "Verificación aprobada: el teléfono quedó asociado.")
                        return self._to_list(request)

            verification = session.scalar(
                _with_unit(select(VerificationRequest).where(VerificationRequest.id == pk))
            )
            if verification is None:
                return Response("Solicitud inexistente", status_code=404)
            chat_id = conversation_id(session, verification.phone_e164)
            context = {
                "title": "Aprobar verificación",
                "verification": verification,
                "building": formatting.building(verification.unit.building.name),
                "created_at": formatting.full(verification.created_at, self.timezone),
                "owners": unit_owners(session, verification.unit_id),
                "pending": verification.status == VerificationRequestStatus.PENDING,
                "status_label": STATUS_LABELS[VerificationRequestStatus(verification.status)],
                "conversation_url": request.url_for(
                    "admin:view-conversation", conversation_id=chat_id
                )
                if chat_id
                else None,
                "error": error,
                "list_url": request.url_for("admin:view-verifications"),
            }
            return await self.templates.TemplateResponse(
                request,
                "verification_approve.html",
                context,
                status_code=400 if error else 200,
            )
