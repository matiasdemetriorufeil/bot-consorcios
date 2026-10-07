"""The tour of "Conversaciones": it opens by itself until each user sees (or skips) it, and the
page's "Ver recorrido" button shows it again over a conversation. Invented data only."""

import json
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import help
from app.db.models import PanelUser, WaConversationStatus
from tests.admin.conftest import (
    NOW,
    OPERATOR,
    OTHER_OPERATOR,
    USER_PASSWORD,
    Panel,
    make_user,
)
from tests.admin.wa_data import conversation


def _auto(panel: Panel, url: str = "/admin/conversations") -> str:
    match = re.search(r'data-tour-auto="(\w+)"', panel.client.get(url).text)
    assert match
    return match.group(1)


def _seen_at(session: Session, username: str) -> object:
    session.expire_all()
    return session.scalar(select(PanelUser.tour_seen_at).where(PanelUser.username == username))


def test_it_opens_by_itself_until_seen(operator: Panel) -> None:
    assert _auto(operator) == "true"
    assert _seen_at(operator.session, OPERATOR) is None

    response = operator.client.post("/admin/conversations/tour-seen")

    assert response.status_code == 204
    assert _seen_at(operator.session, OPERATOR) is not None
    assert _auto(operator) == "false"


def test_each_user_has_her_own(operator: Panel) -> None:
    operator.client.post("/admin/conversations/tour-seen")
    make_user(operator.session, OTHER_OPERATOR)
    operator.session.commit()
    operator.client.get("/admin/logout")
    assert operator.login(OTHER_OPERATOR, USER_PASSWORD).status_code == 302

    assert _auto(operator) == "true"
    assert _seen_at(operator.session, OTHER_OPERATOR) is None
    assert _seen_at(operator.session, OPERATOR) is not None


def test_seen_twice_keeps_the_first_date(operator: Panel) -> None:
    operator.client.post("/admin/conversations/tour-seen")
    first = _seen_at(operator.session, OPERATOR)
    operator.client.post("/admin/conversations/tour-seen")
    assert _seen_at(operator.session, OPERATOR) == first


def test_the_env_admin_keeps_it_in_the_session(logged_in: Panel) -> None:
    assert _auto(logged_in) == "true"
    assert logged_in.client.post("/admin/conversations/tour-seen").status_code == 204
    assert _auto(logged_in) == "false"


def test_how_to_use_shows_it_again(operator: Panel) -> None:
    operator.client.post("/admin/conversations/tour-seen")
    assert _auto(operator, "/admin/conversations?tour=1") == "true"


def test_how_to_use_opens_a_waiting_conversation(operator: Panel) -> None:
    conversation(operator.session, "5493515550701", status=WaConversationStatus.BOT)
    waiting = conversation(
        operator.session,
        "5493515550702",
        status=WaConversationStatus.WAITING_HUMAN,
        handed_off_at=NOW - timedelta(minutes=5),
    )
    operator.session.commit()

    response = operator.client.get("/admin/tour", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].endswith(f"/admin/conversations/{waiting.id}?tour=1")
    page = operator.client.get(response.headers["location"]).text
    # Every step has its element on the page.
    for step in help.TOUR:
        assert f'data-tour="{step["target"]}"' in page, step["target"]


def test_how_to_use_without_conversations(operator: Panel) -> None:
    response = operator.client.get("/admin/tour", follow_redirects=False)
    assert response.headers["location"].endswith("/admin/conversations?tour=1")


def test_the_steps_are_in_the_page(operator: Panel) -> None:
    page = operator.client.get("/admin/conversations").text
    match = re.search(r'<script type="application/json" id="tour-steps">(.*?)</script>', page, re.S)
    assert match
    data = json.loads(match.group(1))
    assert [s["target"] for s in data["steps"]] == ["waiting", "take", "reply", "return", "resolve"]
    assert data["missing"] == help.TOUR_MISSING
    assert 4 <= len(data["steps"]) <= 5


def test_marking_it_needs_login(panel: Panel, db_session: Session) -> None:
    make_user(db_session, OPERATOR)
    db_session.commit()
    response = panel.client.post("/admin/conversations/tour-seen", follow_redirects=False)
    assert response.status_code == 302 and "/admin/login" in response.headers["location"]
    assert _seen_at(db_session, OPERATOR) is None


def test_see_the_tour_is_a_button_of_the_page_not_a_menu_entry(operator: Panel) -> None:
    page = operator.client.get("/admin/conversations").text
    button = re.search(r'<a class="btn btn-sm btn-secondary" id="tour-start" href="([^"]+)"', page)
    assert button and button.group(1).endswith("/admin/tour")
    assert "Ver recorrido" in page and "fa-circle-question" in page
    nav = page.split('id="navbarSupportedContent"', 1)[1].split("</nav>", 1)[0]
    assert "Ver recorrido" not in nav and "Cómo se usa" not in nav


def test_see_the_tour_is_on_an_open_conversation_too(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.WAITING_HUMAN)
    operator.session.commit()
    page = operator.client.get(f"/admin/conversations/{conv.id}").text
    # panel.js starts it right there (the conversation's id is in data-open).
    assert 'id="tour-start"' in page and f'data-open="{conv.id}"' in page


def test_the_guide_points_to_the_button() -> None:
    from app.admin.guide import GUIDE_FILE

    text = GUIDE_FILE.read_text(encoding="utf-8")
    assert "tocá **Ver recorrido** arriba de\n**Conversaciones**" in text
    assert "Cómo se usa" not in text
