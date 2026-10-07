"""The tour of "Conversaciones": always over the made-up example (app.admin.example), never over
a real conversation. A user of the panel is taken there the first time; the .env rescue admin
never. "Cómo usar" opens it again. Invented data only."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PanelUser, WaConversationStatus
from tests.admin.conftest import (
    OPERATOR,
    OTHER_OPERATOR,
    USER_PASSWORD,
    Panel,
    make_user,
)
from tests.admin.wa_data import conversation

EXAMPLE = "/admin/conversations/example"


def _seen_at(session: Session, username: str) -> object:
    session.expire_all()
    return session.scalar(select(PanelUser.tour_seen_at).where(PanelUser.username == username))


def _new_operator(panel: Panel, username: str = OPERATOR) -> Panel:
    make_user(panel.session, username, tour_seen=False)
    panel.session.commit()
    panel.client.get("/admin/logout")
    assert panel.login(username, USER_PASSWORD).status_code == 302
    return panel


def test_the_first_time_goes_to_the_example_keeping_the_tab(panel: Panel) -> None:
    new = _new_operator(panel)

    response = new.client.get("/admin/conversations?tab=mine", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].endswith(f"{EXAMPLE}?tab=mine")
    page = new.client.get(response.headers["location"]).text
    assert 'data-tour-auto="true"' in page and 'data-example="true"' in page


def test_a_real_conversation_too_the_first_time(panel: Panel) -> None:
    new = _new_operator(panel)
    conv = conversation(new.session, status=WaConversationStatus.WAITING_HUMAN)
    new.session.commit()

    response = new.client.get(f"/admin/conversations/{conv.id}", follow_redirects=False)

    assert response.status_code == 302 and EXAMPLE in response.headers["location"]


def test_after_seeing_it_the_real_inbox(panel: Panel) -> None:
    new = _new_operator(panel)
    assert new.client.post("/admin/conversations/tour-seen").status_code == 204
    assert _seen_at(new.session, OPERATOR) is not None

    response = new.client.get("/admin/conversations", follow_redirects=False)

    assert response.status_code == 200
    assert 'data-example="true"' not in response.text
    assert 'data-tour-auto="false"' in response.text


def test_the_poll_never_redirects(panel: Panel) -> None:
    new = _new_operator(panel)
    response = new.client.get("/admin/conversations/poll", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_each_user_has_her_own(panel: Panel) -> None:
    new = _new_operator(panel)
    new.client.post("/admin/conversations/tour-seen")
    other = _new_operator(new, OTHER_OPERATOR)

    response = other.client.get("/admin/conversations", follow_redirects=False)

    assert response.status_code == 302
    assert _seen_at(other.session, OTHER_OPERATOR) is None
    assert _seen_at(other.session, OPERATOR) is not None


def test_seen_twice_keeps_the_first_date(panel: Panel) -> None:
    new = _new_operator(panel)
    new.client.post("/admin/conversations/tour-seen")
    first = _seen_at(new.session, OPERATOR)
    new.client.post("/admin/conversations/tour-seen")
    assert _seen_at(new.session, OPERATOR) == first


def test_the_env_admin_is_never_sent_there(logged_in: Panel) -> None:
    # Its "seen" lives in the session, which starts over at each login: no redirect, ever.
    response = logged_in.client.get("/admin/conversations", follow_redirects=False)
    assert response.status_code == 200 and 'data-example="true"' not in response.text
    # "Cómo usar" opens the example anyway.
    assert logged_in.client.get(f"{EXAMPLE}?tab=waiting").status_code == 200


def test_how_to_use_is_a_button_of_the_page(operator: Panel) -> None:
    page = operator.client.get("/admin/conversations?tab=bot").text
    button = re.search(r'<a class="btn btn-sm btn-secondary" id="tour-start" href="([^"]+)"', page)
    assert button and button.group(1).endswith(f"{EXAMPLE}?tab=bot")
    assert "Cómo usar</a>" in page and "fa-circle-question" in page
    assert "Ver recorrido" not in page
    nav = page.split('id="navbarSupportedContent"', 1)[1].split("</nav>", 1)[0]
    assert "Cómo usar" not in nav and "Cómo se usa" not in nav


def test_the_old_tour_route_is_gone(operator: Panel) -> None:
    assert operator.client.get("/admin/tour").status_code == 404


def test_the_real_inbox_never_starts_the_tour(operator: Panel) -> None:
    conv = conversation(operator.session, status=WaConversationStatus.WAITING_HUMAN)
    operator.session.commit()
    for url in ("/admin/conversations", f"/admin/conversations/{conv.id}?tour=1"):
        page = operator.client.get(url).text
        assert 'data-tour-auto="false"' in page and 'id="tour-steps"' not in page


def test_marking_it_needs_login(panel: Panel, db_session: Session) -> None:
    make_user(db_session, OPERATOR, tour_seen=False)
    db_session.commit()
    response = panel.client.post("/admin/conversations/tour-seen", follow_redirects=False)
    assert response.status_code == 302 and "/admin/login" in response.headers["location"]
    assert _seen_at(db_session, OPERATOR) is None


def test_the_guide_points_to_the_button() -> None:
    from app.admin.guide import GUIDE_FILE

    text = GUIDE_FILE.read_text(encoding="utf-8")
    assert "tocá **Cómo usar** arriba de\n**Conversaciones**" in text
    assert "Ver recorrido" not in text and "Cómo se usa" not in text
