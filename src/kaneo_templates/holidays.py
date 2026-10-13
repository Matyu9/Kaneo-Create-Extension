"""Public holiday calendars, skipped when counting business days (`HOLIDAYS` setting).

Computed locally, no dependency and no network. Each holiday is identified by a key that
the interface translates (`holiday_<key>` in i18n.py).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from functools import lru_cache


def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm, Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - name from the published algorithm
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _france(year: int) -> dict[date, str]:
    sunday = easter(year)
    return {
        date(year, 1, 1): "new_year",
        sunday + timedelta(days=1): "easter_monday",
        date(year, 5, 1): "labour_day",
        date(year, 5, 8): "victory_day",
        sunday + timedelta(days=39): "ascension",
        sunday + timedelta(days=50): "whit_monday",
        date(year, 7, 14): "bastille_day",
        date(year, 8, 15): "assumption",
        date(year, 11, 1): "all_saints",
        date(year, 11, 11): "armistice",
        date(year, 12, 25): "christmas",
    }


def _alsace_moselle(year: int) -> dict[date, str]:
    days = _france(year)
    days[easter(year) - timedelta(days=2)] = "good_friday"
    days[date(year, 12, 26)] = "st_stephen"
    return days


CALENDARS: dict[str, Callable[[int], dict[date, str]]] = {
    "fr": _france,
    "fr-alsace-moselle": _alsace_moselle,
}


class HolidayCalendar:
    """Holidays of one region; empty when `region` is None."""

    def __init__(self, region: str | None) -> None:
        if region is not None and region not in CALENDARS:
            raise ValueError(f"unknown holiday calendar {region!r}")
        self.region = region

    def name(self, d: date) -> str | None:
        """Key of the holiday falling on `d`, or None."""
        if self.region is None:
            return None
        return _year(self.region, d.year).get(d)

    def __contains__(self, d: object) -> bool:
        return isinstance(d, date) and self.name(d) is not None


@lru_cache(maxsize=64)
def _year(region: str, year: int) -> dict[date, str]:
    return CALENDARS[region](year)


NO_HOLIDAYS = HolidayCalendar(None)
