"""The example conversation of "Cómo usar" (app.admin.example): fixed data drawn with the real
templates; it neither reads nor writes real conversations, and none of its buttons sends
anything. Invented data only."""

import html
import json
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.admin import help
from app.db.models import WaContact, WaConversation, WaConversationStatus, WaMessage
from tests.admin.conftest import Panel
from tests.admin.wa_data import conversation, message

EXAMPLE = "/admin/conversations/example"
WA_TABLES = ("wa_conversations", "wa_messages", "wa_contacts")


@pytest.fixture
def statements(db_session: Session) -> Iterator[list[str]]:
    """Every SQL statement run on the test's connection while the test runs."""
    seen: list[str] = []
    connection = db_session.connection()

    def record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        seen.append(statement)

    event.listen(connection, "before_cursor_execute", record)
    yield seen
    event.remove(connection, "before_cursor_execute", record)


def _counts(session: Session) -> tuple[int, int, int]:
    session.expire_all()
    return tuple(  # type: ignore[return-value]
        session.scalar(select(func.count()).select_from(model))
        for model in (WaConversation, WaMessage, WaContact)
    )


def test_it_neither_reads_nor_writes_real_conversations(
    operator: Panel, statements: list[str]
) -> None:
    before = _counts(operator.session)
    statements.clear()

    response = operator.client.get(f"{EXAMPLE}?tab=waiting")

    assert response.status_code == 200
    touched = [s for s in statements if any(table in s for table in WA_TABLES)]
    assert touched == []
    assert _counts(operator.session) == before == (0, 0, 0)


def test_real_and_example_never_mix(operator: Panel) -> None:
    real = conversation(
        operator.session,
        "5493515550999",
        profile_name="Persona Real Inventada",
        status=WaConversationStatus.WAITING_HUMAN,
    )
    message(operator.session, real, "un mensaje de verdad")
    operator.session.commit()

    example = operator.client.get(EXAMPLE).text
    assert "Persona Real Inventada" not in example and "555-0999" not in example
    assert "un mensaje de verdad" not in example
    inbox = operator.client.get("/admin/conversations").text
    assert "Ana Ejemplo" not in inbox and "Persona Real Inventada" in inbox
    search = operator.client.get("/admin/conversations", params={"q": "Ana Ejemplo"}).text
    assert '<span class="inbox-name">Ana Ejemplo</span>' not in search
    assert '<span class="inbox-name">Ana Ejemplo</span>' in example


def test_no_button_of_the_example_posts(operator: Panel) -> None:
    page = operator.client.get(EXAMPLE).text

    assert 'method="POST"' not in page and "method='POST'" not in page
    assert "/action" not in page
    assert 'data-example="true"' in page and "data-poll-url" not in page
    # Every form of the conversation does nothing and says what it would do.
    forms = re.findall(r"<form [^>]*>", page)
    conversation_forms = [f for f in forms if "confirm-dialog" not in f and 'method="dialog"' in f]
    notes = {html.unescape(n) for n in re.findall(r'data-example-note="([^"]*)"', page)}
    for key in ("take", "return", "resolve", "reply", "note", "search", "link"):
        assert help.EXAMPLE_NOTES[key] in notes, key
    assert len(conversation_forms) >= 6
    # The real resolve's confirmation is not there (nothing to confirm).
    assert "data-confirm-title" not in page


def test_what_it_shows(operator: Panel) -> None:
    page = html.unescape(operator.client.get(EXAMPLE).text)
    assert help.EXAMPLE_BANNER in page
    for text in ("Ana Ejemplo", "351 555-0101", "TORRE EJEMPLO · 4-B", "Bruno Prueba"):
        assert text in page, text
    assert "Derivación del bot (nota interna)" in page  # the handoff note
    assert '<span class="msg-choice">Hablar con una persona</span>' in page  # the bot's buttons
    assert "URGENTE" in page and "Urgencia" in page
    assert 'class="contact-card"' in page and "Última deuda" in page
    for target in ("waiting", "take", "reply", "return", "resolve"):
        assert f'data-tour="{target}"' in page, target


def test_the_tour_runs_over_it(operator: Panel) -> None:
    page = operator.client.get(EXAMPLE).text
    assert 'data-tour-auto="true"' in page
    match = re.search(r'<script type="application/json" id="tour-steps">(.*?)</script>', page, re.S)
    assert match
    steps = json.loads(match.group(1))["steps"]
    assert [s["target"] for s in steps] == ["waiting", "take", "reply", "return", "resolve"]


@pytest.mark.parametrize(("tab", "back"), [("mine", "mine"), ("resolved", "resolved"),
                                            ("nada", "waiting"), (None, "waiting")])  # fmt: skip
def test_back_to_the_tab_where_she_was(operator: Panel, tab: str | None, back: str) -> None:
    params = {"tab": tab} if tab else {}
    page = operator.client.get(EXAMPLE, params=params).text
    expected = f'href="https://testserver/admin/conversations?tab={back}" data-example-back'
    assert expected in page
    assert f'data-example-back="https://testserver/admin/conversations?tab={back}"' in page
    assert "Volver a mis conversaciones" in page


def test_how_to_use_from_the_example_keeps_the_tab(operator: Panel) -> None:
    page = operator.client.get(EXAMPLE, params={"tab": "mine"}).text
    assert f'href="https://testserver{EXAMPLE}?tab=mine"' in page


def test_the_example_needs_login(panel: Panel) -> None:
    response = panel.client.get(EXAMPLE, follow_redirects=False)
    assert response.status_code == 302 and "/admin/login" in response.headers["location"]


def test_the_tabs_fit_on_one_line() -> None:
    from app.admin.guide import DOCS_DIR

    css = (DOCS_DIR.parent / "app" / "admin" / "static" / "panel.css").read_text(encoding="utf-8")
    rule = re.search(r"\.inbox-tabs \{[^}]*\}", css)
    assert rule and "flex-wrap: nowrap" in rule.group(0)
    assert "grid-template-columns: 360px" in css
