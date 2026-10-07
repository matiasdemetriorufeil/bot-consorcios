"""Panel page "Conversaciones": the WhatsApp inbox (app.admin.inbox) for every panel user.

- GET  /admin/conversations                      the list (tabs, search)
- GET  /admin/conversations/{id}                 the list + the conversation + the contact card
                                                 (on a phone: only the conversation)
- GET  /admin/conversations/poll                 JSON every few seconds: the list, new messages
                                                 of the open one and what makes the page beep
- POST /admin/conversations/{id}/action          take, return, resolve, note, reply, template
- POST /admin/conversations/tour-seen            the tour (app.admin.help.TOUR) was seen

The tour opens by itself until the user sees (or skips) it: panel_users.tour_seen_at, or the
session for the .env rescue admin (not in that table). The page's "Ver recorrido" button
starts it again right there; with no conversation open it goes through /admin/tour
(app.admin.guide.TourView), which opens the first waiting one with ?tour=1.

The page and the poll render the same Jinja partials (autoescaped: whatever the contact
sends is only ever text). Forms that send something carry a one-time token kept in the
session, so a double click (or a resent form) sends it once.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import anyio
from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.admin import help, inbox
from app.admin.auth import admin_user, current_user_id, display_names
from app.config import Settings
from app.db.models import PanelUser, QuickReply, WaConversationStatus

# The .env rescue admin's "tour seen" (it has no row in panel_users).
SESSION_TOUR_SEEN = "tour_seen"

POLL_SECONDS = 4
# One-time tokens of the send forms remembered per session (a double submit is ignored).
USED_TOKENS_KEPT = 30


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _tab(request: Request) -> str:
    tab = request.query_params.get("tab", inbox.DEFAULT_TAB)
    return tab if tab in inbox.TABS else inbox.DEFAULT_TAB


def new_form_token() -> str:
    return secrets.token_urlsafe(12)


def use_form_token(request: Request, token: str) -> bool:
    """False if the token is missing or was already used (the form was sent twice)."""
    if not token:
        return False
    used = list(request.session.get("used_form_tokens") or [])
    if token in used:
        return False
    request.session["used_form_tokens"] = [*used, token][-USED_TOKENS_KEPT:]
    return True


def tour_seen(session: Any, request: Request) -> bool:
    """Whether the logged-in user already saw (or skipped) the tour."""
    user_id = current_user_id(request)
    if user_id is None:
        return bool(request.session.get(SESSION_TOUR_SEEN))
    user = session.get(PanelUser, user_id)
    return user is None or user.tour_seen_at is not None


def mark_tour_seen(session: Any, request: Request, now: datetime) -> None:
    user_id = current_user_id(request)
    if user_id is None:
        request.session[SESSION_TOUR_SEEN] = True
        return
    user = session.get(PanelUser, user_id)
    if user is not None and user.tour_seen_at is None:
        user.tour_seen_at = now
        session.commit()


class ConversationsView(BaseView):
    name = "Conversaciones"
    icon = "fa-solid fa-comments"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    # Set by setup_admin (staticmethods): the WhatsApp client (None if not configured) and
    # the clock.
    sender_factory: ClassVar[Callable[[], inbox.Sender | None]] = staticmethod(lambda: None)
    clock: ClassVar[Callable[[], datetime]] = staticmethod(lambda: datetime.now(UTC))

    # --- Rendering ------------------------------------------------------------------------

    def _list_context(self, session: Any, request: Request, user: str) -> dict[str, Any]:
        names = display_names(session)
        query = request.query_params.get("q", "").strip()
        tab = _tab(request)
        rows = inbox.list_conversations(session, tab, user, self.clock(), query=query, names=names)
        return {
            "rows": rows,
            "tab": tab,
            "tabs": inbox.TABS,
            "query": query,
            "counts": inbox.tab_counts(session, user),
            "names": names,
        }

    async def _fragment(self, request: Request, name: str, context: dict[str, Any]) -> str:
        template = self.templates.env.get_template(name)
        return await template.render_async({"request": request, **context})

    def _conversation_context(
        self, session: Any, conversation_id: int, names: dict[str, str]
    ) -> dict[str, Any] | None:
        found = inbox.conversation_with_contact(session, conversation_id)
        if found is None:
            return None
        conversation, contact = found
        now = self.clock()
        today = now.astimezone(ZoneInfo(self.timezone)).date()
        messages = inbox.thread(session, conversation.id, names, self.timezone)
        return {
            "conversation": conversation,
            "contact": contact,
            "status_label": inbox.STATUS_LABELS[WaConversationStatus(conversation.status)],
            "assigned_name": names.get(conversation.assigned_to or "", conversation.assigned_to),
            "reason": inbox.reason_label(conversation.handoff_reason),
            "card": inbox.contact_card(session, contact, self.timezone, today),
            "messages": messages,
            "last_id": messages[-1].id if messages else 0,
            "window_open": inbox.window_open(conversation, now),
            "window_closed_text": inbox.WINDOW_CLOSED_TEXT,
            "templates_list": inbox.active_templates(session),
            "quick_replies": list(
                session.scalars(
                    select(QuickReply)
                    .where(QuickReply.active.is_(True))
                    .order_by(QuickReply.sort_order, QuickReply.title)
                )
            ),
            "whatsapp_ready": self.sender_factory() is not None,
        }

    async def _page(self, request: Request, conversation_id: int | None) -> Response:
        user = admin_user(request)
        with self.session_maker() as session:
            if conversation_id is not None:
                inbox.mark_read(session, conversation_id)  # first: the list shows it read
            context = self._list_context(session, request, user)
            open_context: dict[str, Any] = {}
            if conversation_id is not None:
                found = self._conversation_context(session, conversation_id, context["names"])
                if found is None:
                    return Response("Conversación inexistente", status_code=404)
                open_context = found
            # Rendered with the session open: the template reads the rows.
            return await self.templates.TemplateResponse(
                request,
                "conversations.html",
                {
                    "title": "Conversaciones",
                    **context,
                    **open_context,
                    "open_id": conversation_id,
                    "user": user,
                    "form_token": new_form_token(),
                    "poll_seconds": POLL_SECONDS,
                    "tour_auto": request.query_params.get("tour") == "1"
                    or not tour_seen(session, request),
                    "tour_steps": {"steps": help.TOUR, "missing": help.TOUR_MISSING},
                },
            )

    # --- Pages ----------------------------------------------------------------------------

    @expose("/conversations", methods=["GET"], identity="conversations")
    async def conversations_page(self, request: Request) -> Response:
        return await self._page(request, None)

    @expose("/conversations/{conversation_id:int}", methods=["GET"], identity="conversation")
    async def conversation_page(self, request: Request) -> Response:
        return await self._page(request, request.path_params["conversation_id"])

    @expose("/conversations/poll", methods=["GET"], identity="conversations-poll")
    async def poll(self, request: Request) -> Response:
        user = admin_user(request)
        open_id = _int(request.query_params.get("open"))
        after = _int(request.query_params.get("after"))
        visible = request.query_params.get("visible") == "1"
        with self.session_maker() as session:
            if open_id and visible:
                inbox.mark_read(session, open_id)  # she is looking at it
            context = self._list_context(session, request, user)
            data: dict[str, Any] = {
                "counts": context["counts"],
                "waiting_ids": inbox.waiting_ids(session),
                "mine_last": {str(k): v for k, v in inbox.mine_last_inbound(session, user).items()},
            }
            data["inbox_html"] = await self._fragment(
                request, "_inbox_list.html", {**context, "open_id": open_id}
            )
            if open_id:
                found = inbox.conversation_with_contact(session, open_id)
                if found is not None:
                    conversation, _ = found
                    names = context["names"]
                    new = inbox.thread(session, open_id, names, self.timezone, after_id=after)
                    data["conversation"] = {
                        "id": open_id,
                        "status": WaConversationStatus(conversation.status).value,
                        "window_open": inbox.window_open(conversation, self.clock()),
                        "last_id": new[-1].id if new else after,
                        "new_html": await self._fragment(
                            request, "_thread_messages.html", {"messages": new}
                        )
                        if new
                        else "",
                        "header_html": await self._fragment(
                            request,
                            "_conversation_header.html",
                            {
                                "conversation": conversation,
                                "status_label": inbox.STATUS_LABELS[
                                    WaConversationStatus(conversation.status)
                                ],
                                "assigned_name": names.get(
                                    conversation.assigned_to or "", conversation.assigned_to
                                ),
                                "reason": inbox.reason_label(conversation.handoff_reason),
                                "user": user,
                            },
                        ),
                        "statuses": {
                            str(k): v for k, v in inbox.outbound_statuses(session, open_id).items()
                        },
                    }
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @expose("/conversations/tour-seen", methods=["POST"], identity="tour-seen")
    async def tour_seen_page(self, request: Request) -> Response:
        with self.session_maker() as session:
            mark_tour_seen(session, request, self.clock())
        return Response(status_code=204)

    # --- Actions --------------------------------------------------------------------------

    @expose(
        "/conversations/{conversation_id:int}/action",
        methods=["POST"],
        identity="conversation-action",
    )
    async def action(self, request: Request) -> Response:
        conversation_id = request.path_params["conversation_id"]
        form = await request.form()
        what = str(form.get("action", ""))
        user = admin_user(request)
        back = RedirectResponse(
            request.url_for("admin:view-conversation", conversation_id=conversation_id),
            status_code=302,
        )
        sends = what in ("note", "reply", "template")
        if sends and not use_form_token(request, str(form.get("form_token", ""))):
            Flash.warning(request, "Eso ya se había enviado: no se mandó de nuevo.")
            return back
        try:
            ok, message = await anyio.to_thread.run_sync(
                self._act, conversation_id, what, user, form
            )
        except inbox.InboxError as exc:
            ok, message = False, str(exc)
        (Flash.success if ok else Flash.error)(request, message)
        return back

    def _act(self, conversation_id: int, what: str, user: str, form: Any) -> tuple[bool, str]:
        """Runs in a thread (the WhatsApp send blocks). (ok, message to show)."""
        with self.session_maker() as session:
            if what == "take":
                inbox.take(session, conversation_id, user)
                return True, "Tomaste la conversación: el bot ya no responde."
            if what == "return":
                inbox.return_to_bot(session, conversation_id, user)
                return True, "La conversación volvió al bot."
            if what == "resolve":
                inbox.resolve(session, conversation_id, user, self.clock())
                return True, "Conversación resuelta."
            if what == "note":
                inbox.add_note(session, conversation_id, user, str(form.get("text", "")))
                return True, "Nota interna guardada."
            if what in ("reply", "template"):
                sender = self.sender_factory()
                if what == "reply":
                    result = inbox.reply(
                        session, conversation_id, user, str(form.get("text", "")), sender,
                        self.clock(),
                    )  # fmt: skip
                else:
                    result = inbox.send_template(
                        session, conversation_id, user, _int(form.get("template_id")), sender,
                        self.clock(),
                    )  # fmt: skip
                if not result.sent:
                    return False, f"WhatsApp rechazó el mensaje: {result.error}"
                if result.assigned_to_other:
                    other = display_names(session).get(
                        result.assigned_to_other, result.assigned_to_other
                    )
                    return True, f"Mensaje enviado. La conversación sigue asignada a {other}."
                return True, "Mensaje enviado."
        raise inbox.InboxError("Acción desconocida.")
