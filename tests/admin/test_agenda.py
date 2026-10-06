"""The weekly agenda of the SUM (app.admin.agenda): pure, no database. Invented data."""

from datetime import date, time, timedelta

import pytest

from app.admin.agenda import DEFAULT_END_HOUR, DEFAULT_START_HOUR, build_agenda, hour_range
from app.amenities.booking import BookingProblem, SlotDay
from app.db.models import AmenitySlot, Reservation, ReservationSource

MONDAY = date(2026, 10, 5)
DAYS = [MONDAY + timedelta(days=i) for i in range(7)]


def _slot(slot_id: int, weekday: int, start: str, end: str, active: bool = True) -> AmenitySlot:
    return AmenitySlot(
        id=slot_id,
        weekday=weekday,
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        active=active,
    )


def _agenda(slots: list[AmenitySlot], cells: list[SlotDay]):  # noqa: ANN202
    return build_agenda(
        DAYS,
        cells,
        slots,
        today=MONDAY,
        units={7: "04-C"},
        message=lambda problem: f"motivo {problem.value}",
    )


@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        ([], (8, 24)),  # always the whole day
        ([("10:30", "12:00"), ("18:00", "23:15")], (8, 24)),  # inside the day: no change
        ([("06:30", "09:00")], (6, 24)),  # starts before 8: from 06:00
        ([("12:00", "16:00"), ("20:00", "02:00")], (8, 26)),  # overnight: up to 02:00
        ([("22:00", "00:00")], (8, 24)),  # ends exactly at midnight
        ([("23:00", "00:30")], (8, 25)),  # ends 00:30: the 00 row is in
    ],
)
def test_hour_range(slots: list[tuple[str, str]], expected: tuple[int, int]) -> None:
    built = [_slot(i, 0, start, end) for i, (start, end) in enumerate(slots)]
    built.append(_slot(99, 0, "05:00", "07:00", active=False))  # inactive: ignored
    assert hour_range(built) == expected


def test_default_hours_are_constants() -> None:
    assert (DEFAULT_START_HOUR, DEFAULT_END_HOUR) == (8, 24)


def test_each_cell_is_a_block_in_its_day_with_its_state() -> None:
    afternoon = _slot(1, 4, "14:00", "18:00")
    night = _slot(2, 4, "20:00", "02:00")
    friday = DAYS[4]
    reservation = Reservation(id=5, unit_id=7, source=ReservationSource.PANEL)
    cells = [
        SlotDay(friday, afternoon, None, (BookingProblem.TOO_SOON,)),
        SlotDay(friday, night, reservation),
        SlotDay(DAYS[6], _slot(3, 6, "14:00", "18:00"), None),
    ]

    agenda = _agenda([afternoon, night], cells)

    assert agenda.start_hour == 8 and agenda.end_hour == 26
    assert [label for label, _ in agenda.hours][-3:] == ["23:00", "00:00", "01:00"]
    assert [next_day for _, next_day in agenda.hours].count(True) == 2
    assert [len(d.blocks) for d in agenda.days] == [0, 0, 0, 0, 2, 0, 1]
    assert agenda.days[0].is_today and agenda.days[0].short == "Lun"
    blocked, reserved = agenda.days[4].blocks
    assert blocked.state == "blocked" and blocked.reasons == ["motivo too_soon"]
    assert reserved.state == "reserved" and reserved.unit == "04-C"
    # 20:00 to 02:00 over 08:00 to 02:00 (18 hours).
    assert reserved.top == pytest.approx(100 * 12 / 18) and reserved.height == pytest.approx(
        100 / 3
    )
    assert reserved.time == "20:00 a 02:00" and "(del día siguiente)" in reserved.text
    assert agenda.days[6].blocks[0].state == "free"


def test_overlapping_slots_share_the_width() -> None:
    long = _slot(1, 0, "10:00", "14:00")
    overlaps = _slot(2, 0, "12:00", "16:00")
    after = _slot(3, 0, "14:00", "16:00")  # starts when the first ends: reuses its lane
    cells = [SlotDay(MONDAY, s, None) for s in (long, overlaps, after)]

    blocks = {b.cell.slot.id: b for b in _agenda([long, overlaps, after], cells).days[0].blocks}

    assert (blocks[1].lane, blocks[2].lane, blocks[3].lane) == (0, 1, 0)
    assert all(b.lanes == 2 and b.width == 50 for b in blocks.values())
    assert blocks[2].left == 50
