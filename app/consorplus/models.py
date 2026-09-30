from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class Building:
    code: str
    name: str


@dataclass(frozen=True)
class Unit:
    """An option of the unit combo. The label comes as '<unit> | <owner name>'."""

    value: str
    label: str
    # Personal data: kept out of repr so it does not end up in logs by accident.
    owner_name: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DebtLine:
    concept: str
    period: str
    concept_amount: Decimal | None
    balance: Decimal
    accumulated: Decimal | None


@dataclass(frozen=True)
class UnitDebt:
    building_code: str
    unit_value: str
    lines: tuple[DebtLine, ...]

    @property
    def total(self) -> Decimal:
        return sum((line.balance for line in self.lines), Decimal(0))

    @property
    def is_up_to_date(self) -> bool:
        return self.total <= 0
