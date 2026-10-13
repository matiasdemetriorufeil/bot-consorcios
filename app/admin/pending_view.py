"""The panel's first page, «Pendientes», for every panel user (app.admin.pending computes it):

- GET /admin/pending            a greeting and a card per thing that waits for someone of the
                                studio, each with a button to its list, already filtered; for
                                admins, a small row more (last night's reading of ConsorPlus,
                                providers without WhatsApp, what the bot could not answer)
- GET /admin/pending/numbers    JSON: every page of the panel asks for it every few seconds
                                (panel.js: the menu's counters, the tab's title, the alerts);
                                with ?cards=1 (only «Pendientes») also the cards, redrawn

/admin/ and the login open this page (setup_admin).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.admin import formatting, pending
from app.admin.auth import admin_user, display_names, is_admin
from app.config import Settings


def first_name(name: str) -> str:
    return name.split()[0] if name.strip() else name


class PendingView(BaseView):
    name = "Pendientes"
    icon = "fa-solid fa-list-check"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    clock: ClassVar[Callable[[], datetime]] = staticmethod(lambda: datetime.now(UTC))

    def _numbers(self, request: Request, session: Any) -> pending.Pending:
        return pending.pending_numbers(
            session, admin_user(request), self.clock(), self.timezone, admin=is_admin(request)
        )

    async def _cards(self, request: Request, numbers: pending.Pending) -> str:
        template = self.templates.env.get_template("_pending_cards.html")
        return await template.render_async({"request": request, "p": numbers})

    # The menu links to this page: keep it the first one of the class.
    @expose("/pending", methods=["GET"], identity="pending")
    async def pending_page(self, request: Request) -> Response:
        user = admin_user(request)
        now = self.clock()
        with self.session_maker() as session:
            numbers = self._numbers(request, session)
            name = display_names(session).get(user, user)
        return await self.templates.TemplateResponse(
            request,
            "pending.html",
            {
                "title": "Pendientes",
                "greeting": f"{formatting.greeting(now, self.timezone)}, {first_name(name)}",
                "today": formatting.weekday_day(now, self.timezone),
                "p": numbers,
            },
        )

    @expose("/pending/numbers", methods=["GET"], identity="pending-numbers")
    async def pending_numbers(self, request: Request) -> Response:
        with self.session_maker() as session:
            numbers = self._numbers(request, session)
        data = pending.to_json(numbers)
        if request.query_params.get("cards") == "1":
            data["cards_html"] = await self._cards(request, numbers)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})
