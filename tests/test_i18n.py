import re
from pathlib import Path

import pytest

from kaneo_templates.holidays import CALENDARS
from kaneo_templates.i18n import MESSAGES, translator

SRC = Path(__file__).parent.parent / "src" / "kaneo_templates"


def test_languages_have_the_same_keys():
    assert set(MESSAGES["fr"]) == set(MESSAGES["en"])


def test_no_empty_message():
    for lang, messages in MESSAGES.items():
        assert all(value.strip() for value in messages.values()), lang


def test_translator_falls_back_to_key():
    assert translator("fr")("unknown_key") == "unknown_key"


def test_unknown_language_uses_english():
    assert translator("de")("create") == MESSAGES["en"]["create"]


def used_keys():
    """Literal keys passed to t() in templates, and message keys returned by the code."""
    keys = set()
    for path in (SRC / "web" / "templates").glob("*.html"):
        keys |= set(re.findall(r"""(?<![\w.])t\(["']([a-z_-]+)["']\)""", path.read_text()))
    for path in [SRC / "web" / "routes.py", SRC / "creator.py"]:
        text = path.read_text()
        keys |= set(re.findall(r"""(?:error|errors|notice)=\[?["']([a-z_]+)["']""", text))
        keys |= set(re.findall(r"""errors\.append\(["']([a-z_]+)["']\)""", text))
        keys |= set(re.findall(r"""warnings\.append\(["']([a-z_]+)["']\)""", text))
        keys |= set(re.findall(r""": ([a-z_]+)["']\)""", text))
    keys |= {f"priority_{p}" for p in ("no-priority", "low", "medium", "high", "urgent")}
    keys |= {"device_denied", "device_expired"}
    for calendar in CALENDARS.values():
        keys |= {f"holiday_{key}" for key in calendar(2026).values()}
    return keys


@pytest.mark.parametrize("lang", sorted(MESSAGES))
def test_every_used_key_is_translated(lang):
    missing = sorted(used_keys() - set(MESSAGES[lang]))
    assert not missing


def test_used_keys_are_found():
    # Guards the regexes above: they must keep finding keys.
    keys = used_keys()
    assert {"create", "error_csrf", "session_expired", "column_not_found", "past_dates"} <= keys
