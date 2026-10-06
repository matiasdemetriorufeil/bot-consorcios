"""get_building_info and the choice of building texts by token budget. All data is invented."""

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.agent import Agent
from app.bot.building_info import (
    SECTION_TOKENS,
    TOKEN_BUDGET,
    estimate_tokens,
    keywords,
    select_texts,
    split_sections,
)
from app.bot.tools import ToolContext, run_tool
from app.config import Settings
from app.db.models import BotEvent, Building, BuildingInfo, BuildingInfoCategory, PersonRole
from app.sync.live import DebtResult
from tests.bot import factories as f
from tests.llm.fakes import PROVIDERS, Call, Say, last_tool_result, scripted_provider

OWNER_PHONE = "+5493515550301"  # owner in Rodas II only
MULTI_PHONE = "+5493515550302"  # owner in Rodas II and Torre Inventada
UNKNOWN_PHONE = "+5493515550399"
NOW = datetime(2026, 10, 1, 11, 0, tzinfo=ZoneInfo("America/Argentina/Cordoba"))

RULES = """\
Artículo 1 - Ruidos: silencio entre las 22 y las 8 h.
Artículo 2 - Mudanzas: de lunes a viernes de 9 a 18 h, con aviso de 48 horas.
"""
FILLER = (
    "Disposiciones generales: los ocupantes deben conservar el buen estado de las partes "
    "comunes y cumplir las resoluciones de la asamblea. "
)


def _info(title: str, content: str, category: str = "reglamento") -> BuildingInfo:
    return BuildingInfo(title=title, content=content, category=BuildingInfoCategory(category))


def _long_rules() -> str:
    """~40 generic articles (well over the budget) and two specific ones."""
    articles = [f"Artículo {n} - General: {FILLER * 8}" for n in range(1, 41)]
    articles.insert(10, "Artículo 50 - Mascotas: se permiten perros pequeños con correa.")
    articles.insert(30, "Artículo 51 - Pileta: abre de 10 a 20 h en verano.")
    return "\n".join(articles)


# --- Choosing the texts (no database) ----------------------------------------------------


def test_small_texts_go_whole() -> None:
    infos = [_info("Reglamento interno", RULES), _info("Encargado", "Interno 10.", "contactos")]
    chosen = select_texts(infos, "¿se pueden tener perros?")
    assert chosen.mode == "full"
    assert [t["content"] for t in chosen.texts] == [RULES, "Interno 10."]
    assert chosen.texts[0]["source"] == "reglamento interno"
    assert chosen.texts[1]["source"] == "contactos del edificio"


def test_long_texts_send_only_the_relevant_sections() -> None:
    rules = _long_rules()
    assert estimate_tokens(rules) > TOKEN_BUDGET
    infos = [_info("Reglamento", rules), _info("Horarios", "Portería de 8 a 16 h.", "horarios")]

    chosen = select_texts(infos, "¿Puedo tener un perro?")

    assert chosen.mode == "sections"
    [text] = chosen.texts
    assert "Mascotas" in text["content"] and "Pileta" not in text["content"]
    assert chosen.sections_sent == 1 and chosen.sections_total > 40
    assert chosen.other_titles == ["Horarios"]


def test_sections_never_go_over_the_budget() -> None:
    infos = [_info("Reglamento", _long_rules())]
    # Every generic article matches: the budget, not the matches, decides.
    chosen = select_texts(infos, "disposiciones generales de la asamblea")
    assert chosen.mode == "sections"
    assert 0 < chosen.texts_tokens <= TOKEN_BUDGET
    assert chosen.sections_sent < chosen.sections_total


def test_non_contiguous_sections_are_marked() -> None:
    chosen = select_texts([_info("Reglamento", _long_rules())], "mascotas y pileta")
    content = chosen.texts[0]["content"]
    assert content.index("Mascotas") < content.index("[…]") < content.index("Pileta")


def test_nothing_relevant_sends_nothing() -> None:
    chosen = select_texts([_info("Reglamento", _long_rules())], "¿cuándo pintan la fachada?")
    assert chosen.mode == "sections" and chosen.texts == []
    assert chosen.other_titles == ["Reglamento"]


def test_split_sections_at_headings_and_size() -> None:
    text = "Intro.\nArtículo 1 - Uno.\nsigue uno\nCapítulo II\nArtículo 2 - Dos."
    assert split_sections(text) == [
        "Intro.",
        "Artículo 1 - Uno.\nsigue uno",
        "Capítulo II",
        "Artículo 2 - Dos.",
    ]
    long_line = "palabra " * 2000
    pieces = split_sections(long_line)
    assert len(pieces) > 1
    assert all(estimate_tokens(p) <= SECTION_TOKENS for p in pieces)
    assert " ".join(pieces).split() == long_line.split()


def test_keywords_fold_accents_drop_stopwords_and_add_synonyms() -> None:
    words = keywords("¿Se pueden tener PERROS en el edificio?")
    assert "perro" in words and "masco" in words  # synonym of "perro"
    assert not {"se", "puede", "edifi"} & words
    assert "mudan" in keywords("¿Qué horario hay para la mudanza?")


# --- The tool ------------------------------------------------------------------------------


@pytest.fixture
def buildings(db_session: Session) -> dict[str, Building]:
    rodas2 = f.building(db_session, "031 RODAS II")
    rodas2.address = "Calle Ficticia 100"
    rodas1 = f.building(db_session, "032 RODAS I")
    torre = f.building(db_session, "045 TORRE INVENTADA")
    empty = f.building(db_session, "046 LOS ALGARROBOS")
    closed = f.building(db_session, "047 EDIFICIO CERRADO", active=False)
    owner = f.person(db_session, "Ana Ficticia", email="ana@example.com", phone=OWNER_PHONE)
    multi = f.person(db_session, "Bruno Inventado", phone=MULTI_PHONE)
    f.link(db_session, f.unit(db_session, rodas2, "04-C"), owner)
    f.link(db_session, f.unit(db_session, rodas2, "04-B"), multi)
    f.link(db_session, f.unit(db_session, torre, "COC.3"), multi, PersonRole.OWNER)
    db_session.add_all(
        [
            BuildingInfo(building=rodas2, title="Reglamento interno", content=RULES,
                         category=BuildingInfoCategory.REGLAMENTO),
            BuildingInfo(building=rodas1, title="Horario del encargado",
                         content="De 7 a 14 h.", category=BuildingInfoCategory.HORARIOS),
            BuildingInfo(building=torre, title="Reglamento", content=_long_rules(),
                         category=BuildingInfoCategory.REGLAMENTO),
            BuildingInfo(building=closed, title="Reglamento", content=RULES,
                         category=BuildingInfoCategory.REGLAMENTO),
        ]
    )  # fmt: skip
    db_session.commit()
    return {"rodas2": rodas2, "rodas1": rodas1, "torre": torre, "empty": empty}


def _ctx(session: Session, phone: str) -> ToolContext:
    def no_debt(unit_id: int) -> DebtResult:
        raise AssertionError("building info never reads debt")

    return ToolContext(
        session=session,
        phone=phone,
        refresh_debt=no_debt,
        office_hours_text="de lunes a viernes de 9 a 17",
        emergency_contact="guardia al 351 000-0000",
    )


def _info_events(session: Session) -> list[BotEvent]:
    return list(
        session.scalars(
            select(BotEvent).where(BotEvent.event_type == "building_info").order_by(BotEvent.id)
        )
    )


def _ask(session: Session, phone: str, **args: Any) -> dict[str, Any]:
    return run_tool(_ctx(session, phone), "get_building_info", {"question": "x", **args})


def test_own_building_whole_texts_with_studio_and_token_log(
    db_session: Session, buildings: dict[str, Building]
) -> None:
    result = _ask(db_session, OWNER_PHONE, question="¿Hasta qué hora hay que hacer silencio?")

    assert result["status"] == "ok"
    assert result["building"] == "RODAS II" and result["address"] == "Calle Ficticia 100"
    assert result["texts"] == [
        {"title": "Reglamento interno", "source": "reglamento interno", "content": RULES}
    ]
    assert "de dónde sale" in result["how_to_answer"]
    assert result["studio"]["office_hours"] == "de lunes a viernes de 9 a 17"
    assert result["studio"]["emergency_contact"] == "guardia al 351 000-0000"
    # Only building texts: no people, units or debts.
    dumped = json.dumps(result, ensure_ascii=False)
    assert "Ana" not in dumped and "04-C" not in dumped and "example.com" not in dumped

    [event] = _info_events(db_session)
    assert event.payload["tokens"] == estimate_tokens(dumped)
    assert event.payload["mode"] == "full"
    assert event.payload["building_id"] == buildings["rodas2"].id
    assert event.phone_e164 == OWNER_PHONE


def test_anyone_can_ask_by_name_without_verification(
    db_session: Session, buildings: dict[str, Building]
) -> None:
    result = _ask(db_session, UNKNOWN_PHONE, building="rodas 2", question="mudanzas")
    assert result["status"] == "ok" and result["building"] == "RODAS II"
    # A named building wins over the phone's own one (the information is public).
    other = _ask(db_session, OWNER_PHONE, building="Rodas I")
    assert other["building"] == "RODAS I"


def test_several_own_buildings_ask_which_one(
    db_session: Session, buildings: dict[str, Building]
) -> None:
    result = _ask(db_session, MULTI_PHONE, question="¿Se puede tener perro?")
    assert result["status"] == "which_building"
    assert set(result["buildings"]) == {"RODAS II", "TORRE INVENTADA"}
    assert "texts" not in result and "studio" in result
    chosen = _ask(db_session, MULTI_PHONE, building="la torre inventada", question="perro")
    assert chosen["status"] == "ok" and "Mascotas" in chosen["texts"][0]["content"]


def test_unknown_phone_without_building_gets_only_the_studio(
    db_session: Session, buildings: dict[str, Building]
) -> None:
    result = _ask(db_session, UNKNOWN_PHONE, question="¿En qué horario atienden?")
    assert result["status"] == "need_building"
    assert result["studio"]["office_hours"] == "de lunes a viernes de 9 a 17"
    [event] = _info_events(db_session)
    assert event.payload["status"] == "need_building" and event.payload["tokens"] > 0


@pytest.mark.parametrize(
    ("building", "status"),
    [
        ("Rodas", "ambiguous_building"),
        ("Las Magnolias", "building_not_found"),
        ("Edificio Cerrado", "building_not_found"),  # inactive
        ("Los Algarrobos", "no_info"),
    ],
)
def test_building_problems(
    db_session: Session, buildings: dict[str, Building], building: str, status: str
) -> None:
    result = _ask(db_session, UNKNOWN_PHONE, building=building)
    assert result["status"] == status
    assert "texts" not in result and "next_step" in result
    if status == "ambiguous_building":
        assert set(result["buildings"]) == {"RODAS I", "RODAS II"}


def test_long_rules_send_relevant_sections_and_log_them(
    db_session: Session, buildings: dict[str, Building]
) -> None:
    result = _ask(db_session, UNKNOWN_PHONE, building="Torre Inventada", question="¿y la pileta?")
    assert result["status"] == "ok"
    assert "Pileta" in result["texts"][0]["content"]
    assert "Mascotas" not in result["texts"][0]["content"]
    nothing = _ask(db_session, UNKNOWN_PHONE, building="Torre Inventada", question="fachada")
    assert nothing["status"] == "no_match" and nothing["other_texts"] == ["Reglamento"]

    first, second = _info_events(db_session)
    assert first.payload["mode"] == "sections"
    assert first.payload["sections_sent"] == 1
    assert first.payload["tokens"] < TOKEN_BUDGET
    assert second.payload["status"] == "no_match"


def test_old_building_id_argument_is_rejected(
    db_session: Session, buildings: dict[str, Building]
) -> None:
    result = run_tool(
        _ctx(db_session, OWNER_PHONE),
        "get_building_info",
        {"building_id": buildings["rodas2"].id, "question": "x"},
    )
    assert result["status"] == "error" and "building_id" in result["error"]


@pytest.mark.parametrize("name", PROVIDERS)
def test_agent_passes_the_studio_settings(
    name: str, db_session: Session, buildings: dict[str, Building]
) -> None:
    provider, script = scripted_provider(
        name,
        [
            Call("get_building_info", {"question": "¿horario?"}),
            Say("De lunes a viernes de 9 a 17."),
        ],
    )
    settings = Settings(_env_file=None, emergency_contact_text="guardia al 351 000-0000")
    agent = Agent(provider, settings=settings, now=lambda: NOW)

    agent.reply(db_session, UNKNOWN_PHONE, "¿En qué horario atienden?")

    result = last_tool_result(name, script.requests[1])
    assert "de lunes a viernes de 9 a 17" in result
    assert "351 000-0000" in result


def test_a_building_the_person_never_wrote_is_ignored(
    db_session: Session, buildings: dict[str, Building]
) -> None:
    ctx = _ctx(db_session, MULTI_PHONE)  # units in two buildings
    ctx.person_texts = ["¿Hasta qué hora se puede hacer ruido?"]

    chosen = run_tool(ctx, "get_building_info", {"question": "ruidos", "building": "RODAS II"})

    assert chosen["status"] == "which_building"  # the model chose it: asks instead
    [event] = db_session.scalars(
        select(BotEvent).where(BotEvent.event_type == "building_not_named")
    )
    assert event.payload == {"tool_building": "RODAS II"}

    ctx.person_texts.append("En el rodas 2")
    named = run_tool(ctx, "get_building_info", {"question": "ruidos", "building": "RODAS II"})
    assert named["status"] == "ok" and named["building"] == "RODAS II"
