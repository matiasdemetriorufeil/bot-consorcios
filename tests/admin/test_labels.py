"""Every stored value the panel shows has its Spanish text, in one shared dictionary."""

import re
from enum import StrEnum
from pathlib import Path

import pytest

from app.admin import labels
from app.channels.handoff import REASONS
from app.db.models import (
    BuildingInfoCategory,
    DataSource,
    PanelRole,
    PersonRole,
    ReservationSource,
    SyncJob,
    SyncKind,
    SyncStatus,
    VerificationRequestStatus,
    WaConversationStatus,
)

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "enum",
    [
        VerificationRequestStatus,
        DataSource,
        WaConversationStatus,
        BuildingInfoCategory,
        SyncKind,
        SyncJob,
        SyncStatus,
        ReservationSource,
        PersonRole,
        PanelRole,
    ],
)
def test_every_value_of_every_enum_has_its_text(enum: type[StrEnum]) -> None:
    table = labels.ENUM_TABLES[enum]
    for member in enum:
        text = labels.enum_label(member)
        assert member in table and text == table[member]
        assert text and text != member.value
        # Also from the plain stored string.
        assert labels.label(table, member.value) == text


def test_the_phone_sources_as_agreed() -> None:
    assert labels.PHONE_SOURCE == {
        DataSource.CONSORPLUS: "ConsorPlus",
        DataSource.MANUAL: "Cargado a mano",
        DataSource.BOT_VERIFIED: "Por WhatsApp",
    }


def test_every_handoff_reason_has_a_short_label_and_the_long_text() -> None:
    assert labels.REASON_SHORT.keys() == REASONS.keys()
    for code, (_, long) in REASONS.items():
        short, text = labels.reason(code)  # type: ignore[misc]
        assert text == long
        assert 1 <= len(short.split()) <= 3 and len(short) <= len(long)
    assert labels.reason("window_closed") == (
        "Ventana cerrada",
        "Ventana de 24 h cerrada: el bot no pudo responder",
    )
    assert labels.reason("debt_claim")[0] == "Reclamo de deuda"  # type: ignore[index]
    assert labels.reason("algo_nuevo") == ("Otro motivo", REASONS["other"][1])
    assert labels.reason(None) is None


def test_every_tool_of_the_bot_has_a_name() -> None:
    names = set()
    for path in (ROOT / "app" / "bot").glob("*.py"):
        names |= set(re.findall(r'name="([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert names, "no tools found in app/bot"
    assert names <= labels.TOOLS.keys(), f"tools without a name: {names - labels.TOOLS.keys()}"
    assert labels.tool("get_debt") == "Consultar deuda"
    assert labels.tool("una_nueva") == labels.UNKNOWN_TOOL


def test_unknown_values_are_readable() -> None:
    assert labels.humanize("units_skipped") == "Units skipped"
    assert labels.label(labels.SYNC_STATUS, None) == "—"
