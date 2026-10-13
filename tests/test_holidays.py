from datetime import date

import pytest

from kaneo_templates.holidays import CALENDARS, NO_HOLIDAYS, HolidayCalendar, easter
from kaneo_templates.scheduling import add_business_days, compute_due, parse_due


@pytest.mark.parametrize(
    "year, expected",
    [
        (1818, date(1818, 3, 22)),  # earliest possible date
        (1943, date(1943, 4, 25)),  # latest possible date
        (2000, date(2000, 4, 23)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter(year, expected):
    assert easter(year) == expected


def test_easter_is_always_a_sunday_in_range():
    for year in range(1900, 2200):
        d = easter(year)
        assert d.weekday() == 6
        assert date(year, 3, 22) <= d <= date(year, 4, 25)


def test_france_2026():
    cal = HolidayCalendar("fr")
    days = {d: cal.name(d) for d in (date(2026, m, 1) for m in range(1, 13))}
    assert days[date(2026, 1, 1)] == "new_year"
    assert days[date(2026, 5, 1)] == "labour_day"
    assert days[date(2026, 11, 1)] == "all_saints"
    assert cal.name(date(2026, 4, 6)) == "easter_monday"
    assert cal.name(date(2026, 5, 14)) == "ascension"
    assert cal.name(date(2026, 5, 25)) == "whit_monday"
    assert cal.name(date(2026, 7, 14)) == "bastille_day"
    assert cal.name(date(2026, 12, 25)) == "christmas"
    assert cal.name(date(2026, 12, 26)) is None
    assert cal.name(date(2026, 4, 3)) is None


def test_france_has_eleven_holidays_a_year():
    for year in (2024, 2025, 2026, 2030):
        assert len(CALENDARS["fr"](year)) == 11


def test_alsace_moselle_adds_two_days():
    cal = HolidayCalendar("fr-alsace-moselle")
    assert cal.name(date(2026, 4, 3)) == "good_friday"
    assert cal.name(date(2026, 12, 26)) == "st_stephen"
    assert cal.name(date(2026, 7, 14)) == "bastille_day"
    assert len(CALENDARS["fr-alsace-moselle"](2026)) == 13


def test_membership():
    cal = HolidayCalendar("fr")
    assert date(2026, 5, 8) in cal
    assert date(2026, 5, 7) not in cal
    assert "2026-05-08" not in cal
    assert date(2026, 5, 8) not in NO_HOLIDAYS
    assert NO_HOLIDAYS.name(date(2026, 1, 1)) is None


def test_unknown_calendar():
    with pytest.raises(ValueError, match="unknown holiday calendar"):
        HolidayCalendar("xx")


def test_business_days_skip_holidays():
    fr = HolidayCalendar("fr")
    # Monday 11 May 2026: Friday 8 May is a holiday.
    assert add_business_days(date(2026, 5, 11), -1) == date(2026, 5, 8)
    assert add_business_days(date(2026, 5, 11), -1, fr) == date(2026, 5, 7)
    # Week of Ascension (Thursday 14 May 2026).
    assert add_business_days(date(2026, 5, 13), 1, fr) == date(2026, 5, 15)
    assert add_business_days(date(2026, 5, 13), 2, fr) == date(2026, 5, 18)
    # Christmas to New Year 2026: 25 Dec and 1 Jan are skipped.
    assert add_business_days(date(2026, 12, 24), 6, fr) == date(2027, 1, 5)


def test_calendar_days_ignore_holidays():
    fr = HolidayCalendar("fr")
    assert compute_due(date(2026, 5, 15), parse_due("-7d"), fr) == date(2026, 5, 8)


def test_zero_business_days_keeps_a_holiday_end_date():
    fr = HolidayCalendar("fr")
    assert compute_due(date(2026, 5, 8), parse_due("0b"), fr) == date(2026, 5, 8)
