"""Panel page "Chat de prueba": write to the bot as if from a phone. DEVELOPMENT ONLY: the
view is added only with APP_ENV=development and every endpoint answers 404 otherwise. Admins
only (AdminOnly: operators get 403 on every endpoint, the people search included).

- GET  /admin/dev-chat                  pick a phone (invented, or a known person's: search)
- GET  /admin/dev-chat?phone=+549...    the chat as the contact sees it
- GET  /admin/dev-chat/poll             JSON every 2 s: the messages after the last one shown
- POST /admin/dev-chat/send             a message (or an option tapped) of the contact
- POST /admin/dev-chat/restart          deletes the test conversation's messages

What is typed is stored as a WhatsApp delivery would (app.whatsapp.simulator.receive) and
answered by the same WhatsAppBot as the webhook's, in the background. The conversation is one
more in "Conversaciones": an operator can take it, answer, return it to the bot or resolve it,
and what she sends shows up here (it never reaches Meta: the contact is simulated).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

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
from app.config import Settings
from app.db.models import (
    Building,
    Person,
    Phone,
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
        message_id = await anyio.to_thread.run_sync(self._receive, phone, text, tapped, profile)
        bot = self.bot_factory()
        # After the response, like the webhook: the bot answers in the background.
        return JSONResponse(
            {"message_id": message_id}, background=BackgroundTask(bot.process, message_id)
        )

    def _receive(self, phone: str, text: str, tapped: bool, profile: str | None) -> int:
        with self.session_maker() as session:
            message_id = simulator.receive(
                session, phone, text, self.clock(), tapped=tapped, profile_name=profile
            )
            session.commit()
            return message_id

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
