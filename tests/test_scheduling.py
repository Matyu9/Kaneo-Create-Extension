from datetime import date

import pytest

from kaneo_templates.scheduling import (
    DueSyntaxError,
    Offset,
    add_business_days,
    apply_offset,
    compute_due,
    is_weekend,
    parse_due,
    to_kaneo_datetime,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-7d", Offset(-7, "d")),
        ("+3d", Offset(3, "d")),
        ("0d", Offset(0, "d")),
        ("-0d", Offset(0, "d")),
        ("-5b", Offset(-5, "b")),
        ("+2b", Offset(2, "b")),
        ("0b", Offset(0, "b")),
        ("J-7", Offset(-7, "d")),
        ("J+3", Offset(3, "d")),
        ("j-30", Offset(-30, "d")),
        ("J0", Offset(0, "d")),
        ("  -7d  ", Offset(-7, "d")),
    ],
)
def test_parse_due_valid(text, expected):
    assert parse_due(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "7", "-7", "7d", "J7", "-7w", "-7 d", "J-7d", "--7d", "+-3d", "-12345d", "d", "J"],
)
def test_parse_due_invalid(text):
    with pytest.raises(DueSyntaxError):
        parse_due(text)


def test_parse_due_rejects_non_string():
    with pytest.raises(DueSyntaxError):
        parse_due(-7)  # type: ignore[arg-type]


def test_offset_str():
    assert str(Offset(-7, "d")) == "-7d"
    assert str(Offset(3, "b")) == "+3b"
    assert str(Offset(0, "d")) == "0d"


@pytest.mark.parametrize(
    ("end", "due", "expected"),
    [
        (date(2026, 6, 15), "-7d", date(2026, 6, 8)),
        (date(2026, 6, 15), "+3d", date(2026, 6, 18)),
        (date(2026, 6, 15), "0d", date(2026, 6, 15)),
        (date(2026, 3, 5), "-10d", date(2026, 2, 23)),
        (date(2026, 1, 31), "+1d", date(2026, 2, 1)),
        (date(2026, 1, 3), "-5d", date(2025, 12, 29)),
        (date(2026, 12, 30), "+3d", date(2027, 1, 2)),
        (date(2028, 3, 1), "-1d", date(2028, 2, 29)),
        (date(2027, 3, 1), "-1d", date(2027, 2, 28)),
        (date(2028, 2, 28), "+1d", date(2028, 2, 29)),
        (date(2100, 3, 1), "-1d", date(2100, 2, 28)),
        (date(2000, 3, 1), "-1d", date(2000, 2, 29)),
        (date(2026, 6, 15), "J-30", date(2026, 5, 16)),
        (date(2026, 6, 15), "J+3", date(2026, 6, 18)),
    ],
)
def test_calendar_offsets(end, due, expected):
    assert apply_offset(end, parse_due(due)) == expected


# 2026-06-12 is a Friday, 2026-06-13 a Saturday, 2026-06-14 a Sunday, 2026-06-15 a Monday.
@pytest.mark.parametrize(
    ("start", "n", "expected"),
    [
        (date(2026, 6, 15), -1, date(2026, 6, 12)),
        (date(2026, 6, 15), -5, date(2026, 6, 8)),
        (date(2026, 6, 12), 1, date(2026, 6, 15)),
        (date(2026, 6, 12), 5, date(2026, 6, 19)),
        (date(2026, 6, 13), 1, date(2026, 6, 15)),
        (date(2026, 6, 13), -1, date(2026, 6, 12)),
        (date(2026, 6, 14), 1, date(2026, 6, 15)),
        (date(2026, 6, 14), -1, date(2026, 6, 12)),
        (date(2026, 6, 14), -5, date(2026, 6, 8)),
        (date(2026, 6, 13), 0, date(2026, 6, 13)),
        (date(2026, 6, 17), 10, date(2026, 7, 1)),
        (date(2027, 1, 1), -1, date(2026, 12, 31)),
        (date(2026, 12, 31), 2, date(2027, 1, 4)),
    ],
)
def test_business_days(start, n, expected):
    assert add_business_days(start, n) == expected
    assert not is_weekend(expected) or n == 0


def test_business_offset_via_parse():
    assert apply_offset(date(2026, 6, 15), parse_due("-5b")) == date(2026, 6, 8)


def test_compute_due_without_end_or_offset():
    assert compute_due(None, Offset(-7)) is None
    assert compute_due(date(2026, 6, 15), None) is None
    assert compute_due(date(2026, 6, 15), Offset(-7)) == date(2026, 6, 8)


def test_is_weekend():
    assert is_weekend(date(2026, 6, 13))
    assert is_weekend(date(2026, 6, 14))
    assert not is_weekend(date(2026, 6, 15))


def test_to_kaneo_datetime():
    assert to_kaneo_datetime(date(2026, 2, 3)) == "2026-02-03T12:00:00.000Z"


# Start-anchored offsets


@pytest.mark.parametrize(
    "value, expected",
    [
        ("start", Offset(0, "d", "start")),
        ("START", Offset(0, "d", "start")),
        ("start+2d", Offset(2, "d", "start")),
        ("start-3b", Offset(-3, "b", "start")),
        ("start+0d", Offset(0, "d", "start")),
        (" start+10d ", Offset(10, "d", "start")),
    ],
)
def test_parse_start_offsets(value, expected):
    assert parse_due(value) == expected


@pytest.mark.parametrize("value", ["start2d", "start+", "starting", "start J-7", "start+7", "s+2d"])
def test_parse_invalid_start_offsets(value):
    with pytest.raises(DueSyntaxError):
        parse_due(value)


@pytest.mark.parametrize(
    "offset, text",
    [
        (Offset(-7), "-7d"),
        (Offset(0), "0d"),
        (Offset(2, "d", "start"), "start+2d"),
        (Offset(-1, "b", "start"), "start-1b"),
        (Offset(0, "d", "start"), "start"),
    ],
)
def test_offset_str_with_anchor(offset, text):
    assert str(offset) == text


def test_compute_due_uses_the_offset_anchor():
    start, end = date(2026, 3, 2), date(2026, 3, 20)
    assert compute_due(end, parse_due("start+2d"), start=start) == date(2026, 3, 4)
    assert compute_due(end, parse_due("start"), start=start) == start
    assert compute_due(end, parse_due("-2d"), start=start) == date(2026, 3, 18)
    # Business days from a Monday start, backwards: the previous Friday.
    assert compute_due(end, parse_due("start-1b"), start=start) == date(2026, 2, 27)


def test_compute_due_without_its_anchor():
    assert compute_due(date(2026, 3, 20), parse_due("start+2d")) is None
    assert compute_due(None, parse_due("-2d"), start=date(2026, 3, 2)) is None
