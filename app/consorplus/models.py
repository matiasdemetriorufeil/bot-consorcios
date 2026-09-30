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


@dataclass(frozen=True, repr=False)
class RosterContact:
    """A person as ConsorPlus lists them for a unit. Raw cell texts, not normalized.

    Personal data: repr is redacted so it does not end up in logs by accident.
    """

    name: str
    phone: str = ""
    mobile: str = ""
    email: str = ""
    document: str = ""

    def __repr__(self) -> str:
        return "RosterContact(<redacted>)"


@dataclass(frozen=True)
class RosterRow:
    """One unit of 'List. Todos Los Datos' (Listado2036.aspx), reduced to what the bot needs."""

    unit_value: str  # "Id": same value as the unit combo of the debt page
    building_code: str  # "Cod.Edif", e.g. "001"
    building_name: str
    unit_label: str
    ph: str
    unit_type: str = ""  # "Tipo Unidad", e.g. department, garage
    # "Cód.Electrónico": Siro payment code, raw cell text. Kept out of repr and logs.
    payment_code: str = field(default="", repr=False)
    owner: RosterContact | None = field(default=None, repr=False)
    second_owner: RosterContact | None = field(default=None, repr=False)
    tenant: RosterContact | None = field(default=None, repr=False)
