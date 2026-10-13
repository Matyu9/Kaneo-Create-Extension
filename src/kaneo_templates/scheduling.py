"""Due date computation relative to a project end date. Pure functions, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Unit = Literal["d", "b"]

_OFFSET_RE = re.compile(r"^(?P<sign>[+-]?)(?P<n>\d{1,4})(?P<unit>[db])$")
_ALIAS_RE = re.compile(r"^[jJ](?P<sign>[+-]?)(?P<n>\d{1,4})$")
_SYNTAX_MESSAGE = "invalid due offset: expected forms like -7d, +3d, 0d, -5b, J-7, J+3"


class DueSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Offset:
    days: int
    unit: Unit = "d"

    def __str__(self) -> str:
        return f"{self.days:+d}{self.unit}" if self.days else f"0{self.unit}"


def parse_due(value: str) -> Offset:
    """Parse `-7d`, `+3d`, `0d`, `-5b`, `+2b` or the aliases `J-7`, `J+3`, `J0`.

    A sign is required for non-zero offsets so that the direction is always explicit.
    """
    if not isinstance(value, str):
        raise DueSyntaxError(_SYNTAX_MESSAGE)
    text = value.strip()
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


def apply_offset(end: date, offset: Offset) -> date:
    if offset.unit == "d":
        return end + timedelta(days=offset.days)
    return add_business_days(end, offset.days)


def add_business_days(start: date, n: int) -> date:
    """Move `n` working days (Monday to Friday) from `start`.

    Counting starts from `start` whatever its weekday: from a Saturday, `+1` is the
    next Monday and `-1` the previous Friday. `0` returns `start` unchanged.
    """
    step = 1 if n > 0 else -1
    remaining = abs(n)
    current = start
    while remaining:
        current += timedelta(days=step)
        if not is_weekend(current):
            remaining -= 1
    return current


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def compute_due(end: date | None, offset: Offset | None) -> date | None:
    """Due date for a task, or None when there is no offset or no end date."""
    if end is None or offset is None:
        return None
    return apply_offset(end, offset)


def to_kaneo_datetime(d: date) -> str:
    """Kaneo stores a timestamp; noon UTC keeps the same calendar day in every timezone
    from UTC-12 to UTC+11."""
    return f"{d.isoformat()}T12:00:00.000Z"
