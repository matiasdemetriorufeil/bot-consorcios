"""Panel page "Chat de prueba": write to the bot as if from a phone. DEVELOPMENT ONLY: the
view is added only with APP_ENV=development and every endpoint answers 404 otherwise. Admins
only (AdminOnly: operators get 403 on every endpoint, the people search included).

- GET  /admin/dev-chat                  pick a phone (invented, or a known person's: search)
- GET  /admin/dev-chat?phone=+549...    the chat as the contact sees it
- GET  /admin/dev-chat/poll             JSON every 2 s: the messages after the last one shown
- POST /admin/dev-chat/send             a message (or an option tapped) of the contact
- POST /admin/dev-chat/restart          deletes the test conversation's messages
- POST /admin/dev-chat/provider         "Hablar como proveedor": that provider's WhatsApp is
                                        simulated from now on (the claims sent to it show up
                                        here, with their buttons) and the chat opens as it
- POST /admin/dev-chat/claim-jobs       "Correr tareas de reclamos ahora" (app.claims.jobs),
                                        optionally "como si fueran las" another date and time

What is typed is stored as a WhatsApp delivery would (app.whatsapp.simulator.receive) and
answered by the same WhatsAppBot as the webhook's, in the background. The conversation is one
more in "Conversaciones": an operator can take it, answer, return it to the bot or resolve it,
and what she sends shows up here (it never reaches Meta: the contact is simulated).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import anyio
from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import select, update
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.admin import inbox
from app.admin.auth import AdminOnly, display_names, is_admin
from app.admin.conversations import _int
from app.bot.identity import to_e164
from app.bot.unit_search import display_building_name
from app.claims.jobs import run_claim_jobs
from app.config import Settings
from app.db.models import (
    Building,
    Person,
    Phone,
    Provider,
    Unit,
    UnitPerson,
    WaConversationStatus,
    WaDirection,
    WaMessage,
    WaMessageStatus,
)
from app.whatsapp import simulator
from app.whatsapp.bot import WhatsAppBot

POLL_SECONDS = 2
SEARCH_LIMIT = 20
MAX_TEXT = 4096
# Invented by default (the 555 exchange, as in the tests): someone the bot does not know.
DEFAULT_PHONE = "+5493515550000"


def search_people(session: Any, term: str) -> list[dict[str, str]]:
    """People of the database with a usable phone, by name, unit or building."""
    like = f"%{term.strip()}%"
    rows = session.execute(
        select(Person.full_name, Phone.e164, Unit.label, Building.name)
        .join(Phone, Phone.person_id == Person.id)
        .outerjoin(UnitPerson, UnitPerson.person_id == Person.id)
        .outerjoin(Unit, Unit.id == UnitPerson.unit_id)
        .outerjoin(Building, Building.id == Unit.building_id)
        .where(
            Phone.conflict.is_(False),
            (Person.full_name.ilike(like) | Unit.label.ilike(like) | Building.name.ilike(like)),
        )
        .order_by(Person.full_name, Phone.e164, Building.name, Unit.label)
        .limit(SEARCH_LIMIT * 3)
    ).all()
    found: dict[str, dict[str, str]] = {}
    for name, e164, label, building in rows:
        item = found.setdefault(e164, {"name": name, "phone": e164, "units": ""})
        if label:
            unit = f"{display_building_name(building)} {label}"
            item["units"] = f"{item['units']}; {unit}" if item["units"] else unit
    return list(found.values())[:SEARCH_LIMIT]


def _phone(value: object) -> str | None:
    return to_e164(str(value or "").strip())


class DevChatView(AdminOnly, BaseView):
    name = "Chat de prueba"
    icon = "fa-solid fa-mobile-screen"
    # Set by setup_admin (only in development).
    enabled: ClassVar[bool] = False
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    bot_factory: ClassVar[Callable[[], WhatsAppBot]] = staticmethod(lambda: None)  # type: ignore[assignment,return-value]
    clock: ClassVar[Callable[[], datetime]] = staticmethod(lambda: datetime.now(UTC))
    # The claims' WhatsApp messages (setup_admin: the same as "Reclamos"; None without WhatsApp).
    notifier_factory: ClassVar[Any] = None

    def is_visible(self, request: Request) -> bool:
        return self.enabled and is_admin(request)

    def _check(self) -> None:
        if not self.enabled:
            raise HTTPException(404)

    def _messages(self, session: Any, conversation_id: int, after_id: int = 0) -> list[Any]:
        names = display_names(session)
        return [
            m
            for m in inbox.thread(session, conversation_id, names, self.timezone, after_id=after_id)
            if m.side != "note"
        ]

    async def _fragment(self, request: Request, context: dict[str, Any]) -> str:
        template = self.templates.env.get_template("_dev_chat_messages.html")
        return await template.render_async({"request": request, **context})

    # --- Pages ----------------------------------------------------------------------------

    @expose("/dev-chat", methods=["GET"], identity="dev-chat")
    async def page(self, request: Request) -> Response:
        self._check()
        raw_phone = request.query_params.get("phone", "")
        term = request.query_params.get("q", "").strip()
        phone = _phone(raw_phone) if raw_phone else None
        context: dict[str, Any] = {
            "title": "Chat de prueba",
            "default_phone": DEFAULT_PHONE,
            "raw_phone": raw_phone,
            "phone_invalid": bool(raw_phone) and phone is None,
            "term": term,
            "results": [],
            "phone": phone,
            "messages": [],
            "last_id": 0,
            "conversation_id": None,
            "status_label": "",
            "who": None,
            "poll_seconds": POLL_SECONDS,
        }
        with self.session_maker() as session:
            if term:
                context["results"] = search_people(session, term)
            context["providers"] = session.scalars(
                select(Provider)
                .where(Provider.active.is_(True), Provider.whatsapp_e164.is_not(None))
                .order_by(Provider.name)
            ).all()
            if phone:
                known = inbox.identify_many(session, [phone]).get(phone)
                context["who"] = known
                found = simulator.simulated_conversation(session, phone)
                if found is not None:
                    _, conversation = found
                    messages = self._messages(session, conversation.id)
                    context.update(
                        messages=messages,
                        last_id=messages[-1].id if messages else 0,
                        conversation_id=conversation.id,
                        status_label=inbox.STATUS_LABELS[WaConversationStatus(conversation.status)],
                    )
            return await self.templates.TemplateResponse(request, "dev_chat.html", context)

    @expose("/dev-chat/poll", methods=["GET"], identity="dev-chat-poll")
    async def poll(self, request: Request) -> Response:
        self._check()
        phone = _phone(request.query_params.get("phone"))
        after = _int(request.query_params.get("after"))
        data: dict[str, Any] = {"last_id": after, "html": "", "status": "", "conversation_id": None}
        if phone:
            with self.session_maker() as session:
                found = simulator.simulated_conversation(session, phone)
                if found is not None:
                    _, conversation = found
                    # The test phone has the chat open: what was sent to it is read.
                    session.execute(
                        update(WaMessage)
                        .where(
                            WaMessage.conversation_id == conversation.id,
                            WaMessage.direction == WaDirection.OUTBOUND,
                            WaMessage.is_internal_note.is_(False),
                            WaMessage.status.in_([WaMessageStatus.SENT, WaMessageStatus.DELIVERED]),
                        )
                        .values(status=WaMessageStatus.READ, status_at=self.clock())
                    )
                    session.commit()
                    new = self._messages(session, conversation.id, after_id=after)
                    data.update(
                        conversation_id=conversation.id,
                        status=inbox.STATUS_LABELS[WaConversationStatus(conversation.status)],
                        last_id=new[-1].id if new else after,
                        html=await self._fragment(request, {"messages": new}) if new else "",
                    )
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    # --- Actions --------------------------------------------------------------------------

    @expose("/dev-chat/send", methods=["POST"], identity="dev-chat-send")
    async def send(self, request: Request) -> Response:
        self._check()
        form = await request.form()
        phone = _phone(form.get("phone"))
        text = str(form.get("text", "")).strip()
        if phone is None:
            return JSONResponse({"error": "Teléfono inválido."}, status_code=400)
        if not text or len(text) > MAX_TEXT:
            return JSONResponse(
                {"error": "Escribí un mensaje (hasta 4096 letras)."}, status_code=400
            )
        tapped = form.get("tapped") == "1"
        profile = str(form.get("profile_name", "")).strip()[:200] or None
        payload = str(form.get("payload", "")).strip()[:200]
        message_id = await anyio.to_thread.run_sync(
            self._receive, phone, text, tapped, profile, payload
        )
        bot = self.bot_factory()
        # After the response, like the webhook: the bot answers in the background.
        return JSONResponse(
            {"message_id": message_id}, background=BackgroundTask(bot.process, message_id)
        )

    def _receive(
        self, phone: str, text: str, tapped: bool, profile: str | None, payload: str = ""
    ) -> int:
        with self.session_maker() as session:
            message_id = simulator.receive(
                session, phone, text, self.clock(), tapped=tapped, profile_name=profile,
                payload=payload,
            )  # fmt: skip
            session.commit()
            return message_id

    @expose("/dev-chat/provider", methods=["POST"], identity="dev-chat-provider")
    async def as_provider(self, request: Request) -> Response:
        self._check()
        form = await request.form()
        with self.session_maker() as session:
            provider = session.get(Provider, _int(form.get("provider_id")) or 0)
            if provider is None or not provider.active or not provider.whatsapp_e164:
                Flash.error(request, "Elegí un proveedor activo con WhatsApp.")
                return RedirectResponse(str(request.url_for("admin:view-dev-chat")), 302)
            phone = provider.whatsapp_e164
            simulator.simulate_contact(session, phone, provider=True)
            session.commit()
        Flash.info(
            request,
            "Ahora hablás como el proveedor: lo que le mande el sistema aparece acá y no sale "
            "a WhatsApp.",
        )
        return RedirectResponse(
            str(request.url_for("admin:view-dev-chat").include_query_params(phone=phone)), 302
        )

    @expose("/dev-chat/claim-jobs", methods=["POST"], identity="dev-chat-claim-jobs")
    async def claim_jobs(self, request: Request) -> Response:
        """Runs the claims' periodic job now, or "como si fueran las" the given local time
        (to see a scheduled send, a reminder or an alert without waiting)."""
        self._check()
        form = await request.form()
        phone = _phone(form.get("phone")) or ""
        back = str(request.url_for("admin:view-dev-chat").include_query_params(phone=phone))
        at = _local_time(str(form.get("at", "")), self.timezone)
        if at is None:
            Flash.error(request, "Esa fecha y hora no se entiende.")
            return RedirectResponse(back, status_code=302)
        now = at or self.clock()
        notifier = self.notifier_factory(now=lambda: now) if self.notifier_factory else None
        if notifier is None:
            Flash.error(request, "WhatsApp no está configurado en este servidor.")
            return RedirectResponse(back, status_code=302)
        report = await anyio.to_thread.run_sync(
            run_claim_jobs, self.session_maker, notifier, now, self.timezone
        )
        when = now.astimezone(ZoneInfo(self.timezone)).strftime("%d/%m/%Y %H:%M")
        if not report.ran:
            Flash.warning(
                request, "Ya había otra corrida de las tareas de reclamos: probá de nuevo."
            )
        else:
            Flash.success(
                request,
                f"Tareas de reclamos corridas como si fueran las {when}: "
                f"{len(report.sent)} mandado(s) al proveedor, {len(report.reminded)} "
                f"recordatorio(s), {len(report.alerted)} aviso(s) al estudio.",
            )
        return RedirectResponse(back, status_code=302)

    @expose("/dev-chat/restart", methods=["POST"], identity="dev-chat-restart")
    async def restart(self, request: Request) -> Response:
        self._check()
        form = await request.form()
        phone = _phone(form.get("phone"))
        if phone:
            with self.session_maker() as session:
                done = simulator.restart(session, phone)
                session.commit()
            if done:
                Flash.success(request, "Conversación de prueba borrada: empezá de nuevo.")
        return RedirectResponse(
            str(request.url_for("admin:view-dev-chat").include_query_params(phone=phone or "")),
            status_code=302,
        )


def _local_time(value: str, timezone: str) -> datetime | None | bool:
    """ "2026-10-03T08:05" (a datetime-local field, Córdoba time) -> that moment; "" -> False
    (now); None when it cannot be read."""
    if not value.strip():
        return False
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=ZoneInfo(timezone))
    except ValueError:
        return None
