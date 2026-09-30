"""In-memory stand-in for ConsorPlusClient (no network). All data here is invented."""

import threading
from dataclasses import dataclass, field
from decimal import Decimal

from app.consorplus.models import Building as CpBuilding
from app.consorplus.models import DebtLine, RosterContact, RosterRow, UnitDebt

OWNER = RosterContact(name="PEREZ FICTICIO, JUAN", mobile="3515550101")


def roster_row(unit: str, building: str = "007") -> RosterRow:
    return RosterRow(
        unit_value=unit,
        building_code=building,
        building_name=f"{building} CONSORCIO FICTICIO",
        unit_label=f"{unit[-2:]}° A",
        ph=unit[-1],
        unit_type="DPTO",
        owner=OWNER,
    )


def debt(building: str, unit: str, *balances: str) -> UnitDebt:
    lines = []
    accumulated = Decimal(0)
    for month, balance in enumerate(balances, start=1):
        accumulated += Decimal(balance)
        lines.append(
            DebtLine(
                concept="001 EXP.COMUNES",
                period=f"{month:02d}/2026",
                concept_amount=Decimal(balance),
                balance=Decimal(balance),
                accumulated=accumulated,
            )
        )
    return UnitDebt(building_code=building, unit_value=unit, lines=tuple(lines))


@dataclass
class FakeConsorPlus:
    """Rosters by building code; debts by (building, unit). Unknown units have no debt."""

    rosters: dict[str, list[RosterRow] | Exception] = field(default_factory=dict)
    debts: dict[tuple[str, str], UnitDebt | Exception] = field(default_factory=dict)
    # If set, get_debt waits for it (to simulate a slow ConsorPlus).
    gate: threading.Event | None = None
    debt_calls: list[tuple[str, str]] = field(default_factory=list)

    def list_buildings(self) -> list[CpBuilding]:
        return [CpBuilding(code=code, name=f"{code} X") for code in self.rosters]

    def list_roster(self, building_code: str) -> list[RosterRow]:
        result = self.rosters[building_code]
        if isinstance(result, Exception):
            raise result
        return result

    def get_debt(self, building_code: str, unit_value: str) -> UnitDebt:
        self.debt_calls.append((building_code, unit_value))
        if self.gate is not None:
            assert self.gate.wait(timeout=10), "gate never opened"
        result = self.debts.get((building_code, unit_value))
        if isinstance(result, Exception):
            raise result
        return result or UnitDebt(building_code=building_code, unit_value=unit_value, lines=())
