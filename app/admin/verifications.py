"""Panel page "Verificaciones": the registrations left by the bot (request_registration: a
tenant or a relative, or an owner of a unit without owner email), for operators and admins.

- GET       /admin/verifications                   the pending ones, oldest first, with the role
                                                   the person said
- GET/POST  /admin/verifications/{id}/approve      link the phone to an owner already in the
                                                   unit, or create the person with the chosen
                                                   role (app.admin.neighbors_service.add_person),
                                                   in one transaction
- POST      /admin/verifications/{id}/reject       the phone is not registered

Then the neighbor is told by WhatsApp (Notifier.notify_registration): free text in the 24-hour
window; outside it the template registro_aprobado (a rejection is not sent). The panel says
whether it went. Audited in bot_events (verification_approved, verification_rejected,
registration_notified: no texts). The forms carry no CSRF token, like the rest of the panel:
the session cookie is SameSite=Strict (app.admin.auth).
"""

from collections.abc import Callable
from typing import Any, ClassVar

from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin import formatting, help, labels, neighbors_service
from app.admin.audit import log_admin_action
from app.admin.auth import admin_user
from app.admin.fetch import fetch_reply, wants_fetch
from app.admin.views import _in_thread
from app.bot.identity import (
    IdentityError,
    approve_verification_request,
    reject_verification_request,
)
from app.bot.unit_search import display_building_name
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
from app.person_roles import PERSON_ROLE, role_text, role_title

STATUS_LABELS = labels.VERIFICATION_STATUS


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
    """Link the phone to an owner already in the unit (as before)."""
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


def approve_as_new(
    session: Session, request_id: int, role: PersonRole, full_name: str, user: str
) -> int:
    """Create the person with that role in the request's unit and its phone (Vecinos' own
    add_person, which approves the request too), all in one transaction. The person's id."""
    request = session.get(VerificationRequest, request_id, with_for_update=True)
    if request is None or request.status != VerificationRequestStatus.PENDING:
        raise neighbors_service.NeighborError("Esta solicitud ya estaba resuelta.")
    try:
        saved = neighbors_service.add_person(
            session,
            user,
            request.unit_id,
            role,
            full_name=full_name,
            phones=[request.phone_e164],
            commit=False,
            via="verificaciones",
        )
        if isinstance(saved, neighbors_service.PhoneTaken):
            raise neighbors_service.NeighborError(
                f"Ese número ya es de {saved.full_name}: aprobalo vinculándolo o rechazalo."
            )
        log_admin_action(
            session,
            user,
            "verification_approved",
            phone_e164=request.phone_e164,
            request_id=request.id,
            unit_id=request.unit_id,
            person_id=saved.person_id,
            role=str(role),
            new_person=True,
        )
    except Exception:
        session.rollback()
        raise
    session.commit()
    return saved.person_id


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


def where_text(request: VerificationRequest) -> str:
    return f"{display_building_name(request.unit.building.name)} {request.unit.label}"


def notify(
    session: Session,
    notifier: Any,
    request: VerificationRequest,
    user: str,
    *,
    approved: bool,
    role: PersonRole,
) -> str:
    """Tell the neighbor (Notifier.notify_registration) and say in the panel how it went."""
    if notifier is None:
        outcome, text = "no_whatsapp", " No se le avisó: WhatsApp no está configurado."
    else:
        sent = notifier.notify_registration(
            session,
            request.phone_e164,
            approved=approved,
            role=role_text(role),
            unit=where_text(request),
        )
        if sent is None:
            outcome = "window_closed"
            text = " No se le pudo avisar: pasaron más de 24 h desde su último mensaje."
        elif sent.ok:
            outcome, text = "sent", " Se le avisó por WhatsApp."
        else:
            outcome, text = "failed", f" No se le pudo avisar por WhatsApp: {sent.error}"
    log_admin_action(
        session,
        user,
        "registration_notified",
        phone_e164=request.phone_e164,
        request_id=request.id,
        approved=approved,
        outcome=outcome,
    )
    session.commit()
    return text


class VerificationsView(BaseView):
    name = "Verificaciones"
    icon = "fa-solid fa-user-check"
    # The menu's counter (templates/sqladmin/_macros.html, app.admin.pending).
    menu_counter = "verifications"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    # The claims' Notifier (the inbox's WhatsApp client), or None without WhatsApp.
    notifier_factory: ClassVar[Callable[[], Any]] = staticmethod(lambda: None)

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
                    "role": role_title(v.role),
                    "phone": formatting.phone(v.phone_e164),
                    "date": formatting.when(v.created_at, timezone=self.timezone),
                    "status": STATUS_LABELS[VerificationRequestStatus(v.status)],
                }
                for v in pending
            ]
        return await self.templates.TemplateResponse(
            request,
            "verifications.html",
            {"title": "Verificaciones", "rows": rows},
        )

    @expose(
        "/verifications/{request_id:int}/reject", methods=["POST"], identity="verification-reject"
    )
    async def reject_one(self, request: Request) -> Response:
        """As always (a Flash and back to the list) or, asked by panel.js, without reloading:
        the row goes away and the counter changes (app.admin.fetch)."""
        request_id = request.path_params["request_id"]
        user = admin_user(request)
        with self.session_maker() as session:
            try:
                await _in_thread(reject, session, request_id, user)
            except IdentityError as exc:
                session.rollback()
                ok, message = False, problem_text(exc)
            else:
                ok, message = True, "Verificación rechazada: el número no se registró."
                rejected = session.get(VerificationRequest, request_id)
                message += await _in_thread(
                    lambda: notify(
                        session, self.notifier_factory(), rejected, user,
                        approved=False, role=rejected.role,
                    )
                )  # fmt: skip
            pending = session.scalar(
                select(func.count())
                .select_from(VerificationRequest)
                .where(VerificationRequest.status == VerificationRequestStatus.PENDING)
            )
        if wants_fetch(request):
            return fetch_reply(
                ok,
                message,
                remove=[f'[data-verification="{request_id}"]'] if ok else [],
                texts={"[data-pending-count]": str(pending or 0)},
            )
        (Flash.success if ok else Flash.error)(request, message)
        return self._to_list(request)

    @expose(
        "/verifications/{request_id:int}/approve",
        methods=["GET", "POST"],
        identity="verification-approve",
    )
    async def approve_page(self, request: Request) -> Response:
        pk = request.path_params["request_id"]
        error = None
        user = admin_user(request)
        with self.session_maker() as session:
            if request.method == "POST":
                form = await request.form()
                error, role = await self._approve(session, pk, form, user)
                if error is None:
                    approved = session.get(VerificationRequest, pk)
                    told = await _in_thread(
                        lambda: notify(
                            session, self.notifier_factory(), approved, user,
                            approved=True, role=role,
                        )
                    )  # fmt: skip
                    Flash.success(request, f"Verificación aprobada: quedó registrado.{told}")
                    return self._to_list(request)

            verification = session.scalar(
                _with_unit(select(VerificationRequest).where(VerificationRequest.id == pk))
            )
            if verification is None:
                raise HTTPException(status_code=404)
            chat_id = conversation_id(session, verification.phone_e164)
            context = {
                "title": "Aprobar verificación",
                "verification": verification,
                "building": formatting.building(verification.unit.building.name),
                "created_at": formatting.full(verification.created_at, self.timezone),
                "owners": unit_owners(session, verification.unit_id),
                "roles": [(str(r), role_title(r)) for r in PERSON_ROLE],
                "declared": verification.role,
                "declared_label": role_title(verification.role),
                "confirm": help.confirm_attrs("approve_registration"),
                "owner_note": help.REGISTRATION_OWNER_NOTE,
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
            if request.method == "POST":
                context["form"] = form
            return await self.templates.TemplateResponse(
                request,
                "verification_approve.html",
                context,
                status_code=400 if error else 200,
            )

    async def _approve(
        self, session: Session, pk: int, form: Any, user: str
    ) -> tuple[str | None, PersonRole]:
        """The approval the form chose: link to an owner, or a new person with a role. (error,
        the role it got). Only the chosen option's fields count; checked here, not in the
        browser."""
        mode = form.get("mode")
        if mode == "create":
            try:
                role = PersonRole(str(form.get("role", "")))
            except ValueError:
                return "Elegí el rol.", PersonRole.OWNER
            name = str(form.get("full_name", ""))
            try:
                await _in_thread(approve_as_new, session, pk, role, name, user)
            except neighbors_service.NeighborError as exc:
                return str(exc), role
            return None, role
        if mode != "link":
            return "Elegí cómo queda registrado.", PersonRole.OWNER
        verification = session.get(VerificationRequest, pk)
        if (
            verification is not None
            and verification.status == VerificationRequestStatus.PENDING
            and not unit_owners(session, verification.unit_id)
        ):
            return (
                "Esta unidad no tiene propietarios cargados: cargalo como persona nueva.",
                PersonRole.OWNER,
            )
        try:
            person_id = int(str(form.get("person_id", "")))
        except ValueError:
            return "Elegí el propietario.", PersonRole.OWNER
        try:
            await _in_thread(approve, session, pk, person_id, user)
        except IdentityError as exc:
            session.rollback()
            return problem_text(exc), PersonRole.OWNER
        return None, PersonRole.OWNER
