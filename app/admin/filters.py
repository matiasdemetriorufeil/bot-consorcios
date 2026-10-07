"""SQLAdmin's filters with their options in Spanish: SQLAdmin builds "All / Yes / No" and the
values' texts in Python, outside its templates' translations (app.admin.i18n)."""

from collections.abc import Callable
from typing import Any

from sqladmin.filters import BooleanFilter, ForeignKeyFilter, StaticValuesFilter
from sqlalchemy import Select
from starlette.requests import Request

ALL = "Todos"


class YesNoFilter(BooleanFilter):
    async def lookups(
        self, request: Request, model: Any, run_query: Callable[[Select], Any]
    ) -> list[tuple[str, str]]:
        return [("all", ALL), ("true", "Sí"), ("false", "No")]


class ValuesFilter(StaticValuesFilter):
    """values: (stored value, Spanish text)."""

    async def lookups(
        self, request: Request, model: Any, run_query: Callable[[Select], Any]
    ) -> list[tuple[str, str]]:
        return [("__all", ALL), *self.values]


class RelatedFilter(ForeignKeyFilter):
    """By a related row, its text shown through display (e.g. a building without its code)."""

    def __init__(self, *args: Any, display: Callable[[str], str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.display = display

    async def lookups(
        self, request: Request, model: Any, run_query: Callable[[Select], Any]
    ) -> list[tuple[str, str]]:
        options = await super().lookups(request, model, run_query)
        rest = [(value, self.display(text)) for value, text in options if value != "__all"]
        return [("__all", ALL), *sorted(rest, key=lambda option: option[1])]
