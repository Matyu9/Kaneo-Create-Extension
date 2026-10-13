"""Due date computation relative to the project end date (or start date). Pure functions."""

from __future__ import annotations

import re
from collections.abc import Container
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Unit = Literal["d", "b"]
Anchor = Literal["end", "start"]

_OFFSET_RE = re.compile(r"^(?P<sign>[+-]?)(?P<n>\d{1,4})(?P<unit>[db])$")
_ALIAS_RE = re.compile(r"^[jJ](?P<sign>[+-]?)(?P<n>\d{1,4})$")
_START_RE = re.compile(r"^start(?P<rest>[+-]\d{1,4}[db])?$", re.IGNORECASE)
_SYNTAX_MESSAGE = (
    "invalid due offset: expected forms like -7d, +3d, 0d, -5b, J-7, J+3, start+2d, start"
)


class DueSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Offset:
    days: int
    unit: Unit = "d"
    anchor: Anchor = "end"

    def __str__(self) -> str:
        text = f"{self.days:+d}{self.unit}" if self.days else f"0{self.unit}"
        return f"start{text if self.days else ''}" if self.anchor == "start" else text


def parse_due(value: str) -> Offset:
    """Parse `-7d`, `+3d`, `0d`, `-5b`, `+2b` or the aliases `J-7`, `J+3`, `J0`, relative
    to the end date, or `start`, `start+2d`, `start-1b`, relative to the start date.

    A sign is required for non-zero offsets so that the direction is always explicit.
    """
    if not isinstance(value, str):
        raise DueSyntaxError(_SYNTAX_MESSAGE)
    text = value.strip()
    start = _START_RE.match(text)
    if start:
        rest = start["rest"]
        if rest is None:
            return Offset(0, "d", "start")
        offset = parse_due(rest)
        return Offset(offset.days, offset.unit, "start")
    match = _OFFSET_RE.match(text)
    unit: Unit
    if match:
        unit = "b" if match["unit"] == "b" else "d"
    else:
        match = _ALIAS_RE.match(text)
        if not match:
            raise DueSyntaxError(_SYNTAX_MESSAGE)
        unit = "d"
    n = int(match["n"])
    if n != 0 and not match["sign"]:
        raise DueSyntaxError(f"due offset needs a sign, e.g. '+{n}{unit}' or '-{n}{unit}'")
    return Offset(-n if match["sign"] == "-" else n, unit)


def apply_offset(end: date, offset: Offset, holidays: Container[date] = ()) -> date:
    if offset.unit == "d":
        return end + timedelta(days=offset.days)
    return add_business_days(end, offset.days, holidays)


def add_business_days(start: date, n: int, holidays: Container[date] = ()) -> date:
    """Move `n` working days (Monday to Friday, minus `holidays`) from `start`.

    Counting starts from `start` whatever its weekday: from a Saturday, `+1` is the
    next Monday and `-1` the previous Friday. `0` returns `start` unchanged.
    """
    step = 1 if n > 0 else -1
    remaining = abs(n)
    current = start
    while remaining:
        current += timedelta(days=step)
        if not is_weekend(current) and current not in holidays:
            remaining -= 1
    return current


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def compute_due(
    end: date | None,
    offset: Offset | None,
    holidays: Container[date] = (),
    start: date | None = None,
) -> date | None:
    """Due date for a task, or None when there is no offset or its anchor date is missing."""
    if offset is None:
        return None
    anchor = start if offset.anchor == "start" else end
    if anchor is None:
        return None
    return apply_offset(anchor, offset, holidays)


def to_kaneo_datetime(d: date) -> str:
    """Kaneo stores a timestamp; noon UTC keeps the same calendar day in every timezone
    from UTC-12 to UTC+11."""
    return f"{d.isoformat()}T12:00:00.000Z"
