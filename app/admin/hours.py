"""The panel's one way to pick an hour of the day: a list from 00:00 to 23:30 every 30 minutes,
never typed by hand. Used by HourField (SQLAdmin's forms: «Configuración del bot» and
«Configuración de reclamos») and by the template macro hour_select (sqladmin/_macros.html: the
SUM's slots), both from hour_choices.

A value already stored off the list (a slot until 23:59, an hour loaded as 20:15) is still shown,
picked, and accepted when saved unchanged: nothing stored is lost. A new value must be one of
the list (a form sent by hand with "07:13" is refused). SQLAdmin builds the form of a save
without the record, so HourField only checks the format there; the views check the list against
the stored value in on_model_change (check_listed).
"""

from typing import Any

from wtforms import SelectField, ValidationError

from app.bot.bot_config import parse_hour

STEP_MINUTES = 30
HALF_HOURS = [f"{m // 60:02d}:{m % 60:02d}" for m in range(0, 24 * 60, STEP_MINUTES)]
NOT_IN_LIST = "Elegí una hora de la lista."


def hour_choices(current: Any = None, *, empty: str | None = None) -> list[tuple[str, str]]:
    """(value, text) for a <select>: the half hours, plus `current` in its place when it is a
    valid HH:MM off the list; first an empty option (value "") when `empty` is its text."""
    hours = list(HALF_HOURS)
    stored = parse_hour(str(current)) if current else None
    if stored and stored not in hours:
        hours = sorted([*hours, stored])
    choices = [("", empty)] if empty is not None else []
    return choices + [(hour, hour) for hour in hours]


def accepted_hour(value: Any, stored: Any = None) -> str | None:
    """The value as HH:MM when it is one of the list or the one already stored, else None."""
    hour = parse_hour(str(value or ""))
    if hour is None:
        return None
    if hour in HALF_HOURS or hour == parse_hour(str(stored or "")):
        return hour
    return None


def check_listed(data: dict[str, Any], model: Any, names: tuple[str, ...]) -> None:
    """In a view's on_model_change: each hour is one of the list or the one already stored in
    the record (ValueError(NOT_IN_LIST) otherwise: SQLAdmin shows it over the form)."""
    for name in names:
        value = data.get(name)
        if value and accepted_hour(value, getattr(model, name, None)) is None:
            raise ValueError(NOT_IN_LIST)


class HourField(SelectField):
    """A select of hours for SQLAdmin's forms (stored as "HH:MM"). empty: the text of an empty
    option (the field may stay empty: a default applies), or None (an hour is required)."""

    def __init__(self, *args: Any, empty: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("choices", hour_choices(empty=empty))
        kwargs["validate_choice"] = False  # the format here; the list: check_listed
        super().__init__(*args, **kwargs)
        # A select looks like one (SQLAdmin gives every field "form-control": no arrow).
        self.render_kw = {**(self.render_kw or {}), "class": "form-select"}
        self.empty = empty
        self.stored: str | None = None

    def process_data(self, value: Any) -> None:
        self.stored = parse_hour(str(value)) if value else None
        self.choices = hour_choices(self.stored, empty=self.empty)
        super().process_data(self.stored or "")

    def process_formdata(self, valuelist: list[Any]) -> None:
        super().process_formdata(valuelist)
        self.data = str(self.data or "").strip()

    def pre_validate(self, form: Any) -> None:
        if not self.data:
            if self.empty is None:
                raise ValidationError(NOT_IN_LIST)
            return
        # The list itself: check_listed, with the stored value (not known here on a save).
        hour = parse_hour(str(self.data))
        if hour is None:
            raise ValidationError(NOT_IN_LIST)
        self.data = hour
