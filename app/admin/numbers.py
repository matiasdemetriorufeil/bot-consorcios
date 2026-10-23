"""The panel's numbers of settings, never typed by hand when a list says more: a list of values
with a text each (Options: «4 horas», «Sin límite»), picked from a <select>, or, where a list
makes no sense (the order of «Respuestas rápidas»), a whole number between a minimum and a
maximum (Bounds). One component in two forms:

- ChoiceNumberField / NumberField: SQLAdmin's forms («Configuración de reclamos», «Respuestas
  rápidas»);
- the template macro number_select (sqladmin/_macros.html, with choices_of): the SUM's set-up.

Checked on the server, with a message in Spanish next to the field: a value of the list (or in
the bounds); empty only where it means something («Sin límite»). A value already stored off the
list (or out of the bounds) is shown, picked, and accepted when saved unchanged: nothing stored
is lost. A different one, sent by hand, is refused.

SQLAdmin builds the form of a save without the record: RemembersRecord keeps the record it
loads for editing (get_object_for_edit, called before the form is validated) in stored_record,
so the fields know the stored value while validating.
"""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from wtforms import Field, SelectField, ValidationError, widgets

# The record being edited in this request (SQLAdmin's edit), or None (a new one).
stored_record: ContextVar[Any] = ContextVar("stored_record", default=None)
NOT_IN_LIST = "Elegí una opción de la lista."


@dataclass(frozen=True)
class Options:
    """The values of a list and how each one reads: «1 hora» / «4 horas» (unit: singular and
    plural), or its own text (special: {0: "Sin mínimo"}). empty: the text of the option without
    a value (None is stored: «Sin límite»), or None (a value is required)."""

    values: tuple[int, ...]
    unit: tuple[str, str]
    special: Mapping[int, str] = field(default_factory=dict)
    empty: str | None = None

    def label(self, value: int) -> str:
        if value in self.special:
            return self.special[value]
        return f"{value} {self.unit[0] if value == 1 else self.unit[1]}"

    def choices(self, stored: Any = None) -> list[tuple[str, str]]:
        """(value, text) for a <select>, with a stored value off the list in its place."""
        values = list(self.values)
        if isinstance(stored, int) and stored not in values:
            values = sorted([*values, stored])
        empty = [("", self.empty)] if self.empty is not None else []
        return empty + [(str(v), self.label(v)) for v in values]


@dataclass(frozen=True)
class Bounds:
    low: int
    high: int
    optional: bool = False  # may stay empty

    def text(self) -> str:
        return f"un número entero de {self.low} a {self.high}"


HOURS = ("hora", "horas")
DAYS = ("día", "días")
# «Configuración de reclamos».
CLAIM_HOURS = Options((1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24, 36, 48, 72), HOURS)
CLAIM_MINUTES = Options((5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240), ("minuto", "minutos"))
CLAIM_DAYS = Options((1, 2, 3, 4, 5, 6, 7, 10, 14, 21, 30), DAYS)
# The SUM's set-up.
_SUM_HOURS = (0, 1, 2, 3, 6, 12, 24, 48, 72, 96, 120, 168)
SUM_MIN_ADVANCE_HOURS = Options(_SUM_HOURS, HOURS, {0: "Sin mínimo"})
SUM_CANCEL_HOURS = Options(_SUM_HOURS, HOURS, {0: "Hasta que empieza"})
SUM_MAX_ADVANCE_DAYS = Options((1, 2, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365), DAYS)
SUM_PER_UNIT_MONTH = Options(
    (*range(1, 11), 15, 20, 31), ("reserva", "reservas"), empty="Sin límite"
)
# «Respuestas rápidas»: an order, not a list to pick from.
QUICK_REPLY_ORDER = Bounds(0, 999)


def whole(raw: Any) -> int | None:
    """ "12" -> 12; None for "", "1.5", "-3", "1e2", "doce" (no number of the panel goes below
    0: a minus sign is never needed)."""
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isascii() and text.isdigit() else None


def choices_of(options: Options, stored: Any = None) -> list[tuple[str, str]]:
    """The options of a <select> (the template macro number_select)."""
    return options.choices(stored)


def check_choice(raw: Any, options: Options, stored: Any = None) -> int | None:
    """The value picked: one of the list, or the one already stored; None only for the empty
    option where there is one. ValueError(NOT_IN_LIST) otherwise."""
    text = str(raw if raw is not None else "").strip()
    if not text and options.empty is not None:
        return None
    number = whole(text)
    if number is not None and (number in options.values or number == stored):
        return number
    raise ValueError(NOT_IN_LIST)


def check_number(raw: Any, bounds: Bounds, stored: Any = None) -> int | None:
    """The number typed, checked: None only when it may stay empty and it is. ValueError with
    the message otherwise. The value already stored passes unchanged even out of the bounds."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        if bounds.optional:
            return None
        raise ValueError(f"Completalo con {bounds.text()}.")
    number = whole(text)
    if number is None or not bounds.low <= number <= bounds.high:
        if number is not None and stored is not None and number == stored:
            return number
        raise ValueError(f"Tiene que ser {bounds.text()}.")
    return number


def input_attrs(bounds: Bounds, stored: Any = None) -> dict[str, str]:
    """min, max and step of the <input type="number">, widened to a stored value out of them."""
    low, high = bounds.low, bounds.high
    if isinstance(stored, int):
        low, high = min(low, stored), max(high, stored)
    return {"type": "number", "inputmode": "numeric", "min": str(low), "max": str(high),
            "step": "1"}  # fmt: skip


def _stored(name: str) -> int | None:
    record = stored_record.get()
    value = getattr(record, name, None) if record is not None else None
    return value if isinstance(value, int) else None


class ChoiceNumberField(SelectField):
    """A number picked from Options, for SQLAdmin's forms (form_overrides, form_args
    {"options": ...}); the views mix in RemembersRecord so a stored value is known."""

    def __init__(self, *args: Any, options: Options, **kwargs: Any) -> None:
        kwargs["choices"] = options.choices()
        kwargs["validate_choice"] = False  # check_choice, with the stored value
        super().__init__(*args, **kwargs)
        # A select looks like one (SQLAdmin gives every field "form-control": no arrow).
        self.render_kw = {**(self.render_kw or {}), "class": "form-select"}
        self.options = options
        self.stored: int | None = None

    def process_data(self, value: Any) -> None:
        self.stored = value if isinstance(value, int) else None
        self.choices = self.options.choices(self.stored)
        super().process_data("" if self.stored is None else str(self.stored))

    def process_formdata(self, valuelist: list[Any]) -> None:
        super().process_formdata(valuelist)
        self.stored = _stored(self.name)
        self.choices = self.options.choices(self.stored)

    def pre_validate(self, form: Any) -> None:
        try:
            self.data = check_choice(self.data, self.options, self.stored)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


class _NumberInput(widgets.NumberInput):
    """WTForms' <input type="number"> with the field's bounds (widened to its stored value)."""

    def __call__(self, field: Any, **kwargs: Any) -> Any:
        for key, value in input_attrs(field.bounds, field.stored).items():
            if key != "type":
                kwargs.setdefault(key, value)
        if not field.bounds.optional:
            kwargs.setdefault("required", True)
        return super().__call__(field, **kwargs)


class NumberField(Field):
    """A whole number between bounds, for SQLAdmin's forms (form_overrides, form_args
    {"bounds": ...}); the views mix in RemembersRecord so a stored value is known."""

    widget = _NumberInput()

    def __init__(self, *args: Any, bounds: Bounds, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.bounds = bounds
        self.stored: int | None = None
        self.raw = ""

    def process_data(self, value: Any) -> None:
        self.stored = value if isinstance(value, int) else None
        self.data = self.stored

    def process_formdata(self, valuelist: list[Any]) -> None:
        self.raw = str(valuelist[0]).strip() if valuelist else ""
        self.stored = _stored(self.name)
        self.data = whole(self.raw)

    def _value(self) -> str:
        if self.raw:
            return self.raw
        return "" if self.data is None else str(self.data)

    def pre_validate(self, form: Any) -> None:
        raw = self.raw if self.raw or self.data is None else str(self.data)
        try:
            self.data = check_number(raw, self.bounds, self.stored)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


class RemembersRecord:
    """Mixin (before ModelView) for views with these fields: keeps the record SQLAdmin loads for
    editing, so the fields know its stored values while the save is validated."""

    async def get_object_for_edit(self, request: Any) -> Any:
        record = await super().get_object_for_edit(request)  # type: ignore[misc]
        stored_record.set(record)
        return record
