"""The weekly agenda of a SUM in the panel: what to draw, with no rules of its own.

Columns are the 7 days, rows the hours of the whole day: DEFAULT_START_HOUR to
DEFAULT_END_HOUR, widened to fit every active slot of the amenity (one starting earlier, or an
overnight one that pushes the end past midnight, e.g. 26 = 02:00 of the next day). Each slot
of a day is a block over its hours, in the column of the day it STARTS. Its state comes from
app.amenities.booking.availability (called with now): reserved, free (bookable) or blocked
(the service says why). Overlapping slots of a day share the width side by side (lanes).
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, time

from app.amenities.booking import BookingProblem, SlotDay, describe_slot
from app.db.models import AmenitySlot

# The grid always shows at least these hours (24 = midnight).
DEFAULT_START_HOUR = 8
DEFAULT_END_HOUR = 24
WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
WEEKDAYS_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


@dataclass
class Block:
    cell: SlotDay
    state: str  # "reserved" | "free" | "blocked"
    time: str  # "20:00 a 02:00": one line, always visible
    text: str  # the slot's full description (next day, label)
    top: float  # % of the day's height
    height: float
    reasons: list[str] = field(default_factory=list)  # why it is blocked
    unit: str | None = None  # the unit that reserved it
    lane: int = 0
    lanes: int = 1

    @property
    def left(self) -> float:
        return 100 * self.lane / self.lanes

    @property
    def width(self) -> float:
        return 100 / self.lanes


@dataclass
class AgendaDay:
    day: date
    name: str
    short: str
    is_today: bool
    blocks: list[Block]


@dataclass
class Agenda:
    start_hour: int
    end_hour: int  # may pass 24 (next day)
    days: list[AgendaDay]

    @property
    def hours(self) -> list[tuple[str, bool]]:
        """(label, whether it is of the next day) of every row."""
        return [(f"{h % 24:02d}:00", h >= 24) for h in range(self.start_hour, self.end_hour)]


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _span(slot: AmenitySlot) -> tuple[int, int]:
    """Start and end of the slot in minutes from the midnight of the day it starts."""
    start, end = _minutes(slot.start_time), _minutes(slot.end_time)
    return start, end + 24 * 60 if end <= start else end


def hour_range(slots: Sequence[AmenitySlot]) -> tuple[int, int]:
    """First and last hour of the grid: the default ones, widened to fit every active slot
    (the last may pass 24)."""
    spans = [_span(s) for s in slots if s.active]
    first = min([DEFAULT_START_HOUR, *(start // 60 for start, _ in spans)])
    last = max([DEFAULT_END_HOUR, *(math.ceil(end / 60) for _, end in spans)])
    return first, last


def _lanes(blocks: list[Block]) -> None:
    """Side by side when they overlap: each block gets the first lane free at its start."""
    ends: list[float] = []  # end (top + height) of the last block of each lane
    for block in sorted(blocks, key=lambda b: (b.top, -b.height)):
        lane = next((i for i, end in enumerate(ends) if end <= block.top), len(ends))
        if lane == len(ends):
            ends.append(0)
        ends[lane] = block.top + block.height
        block.lane = lane
    for block in blocks:
        block.lanes = max(1, len(ends))


def build_agenda(
    days: Sequence[date],
    cells: Sequence[SlotDay],
    slots: Sequence[AmenitySlot],
    *,
    today: date,
    units: dict[int, str],
    message: Callable[[BookingProblem], str],
) -> Agenda:
    """cells: availability of the days, with now (so blocked ones carry their problems)."""
    first, last = hour_range(slots)
    total = (last - first) * 60
    agenda_days = []
    for day in days:
        blocks = []
        for cell in (c for c in cells if c.day == day):
            start, end = _span(cell.slot)
            block = Block(
                cell=cell,
                state="free",
                time=f"{cell.slot.start_time:%H:%M} a {cell.slot.end_time:%H:%M}",
                text=describe_slot(cell.slot),
                top=100 * (start - first * 60) / total,
                height=100 * (end - start) / total,
            )
            if cell.reservation is not None:
                block.state = "reserved"
                block.unit = units.get(cell.reservation.unit_id, "?")
            elif cell.problems:
                block.state = "blocked"
                block.reasons = [message(p) for p in cell.problems]
            blocks.append(block)
        _lanes(blocks)
        agenda_days.append(
            AgendaDay(
                day, WEEKDAYS[day.weekday()], WEEKDAYS_SHORT[day.weekday()], day == today, blocks
            )
        )
    return Agenda(first, last, agenda_days)
