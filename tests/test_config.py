from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kaneo_templates.config import AuthMode, ConfigError, load_settings
from kaneo_templates.main import create_app

BASE = {"KANEO_BASE_URL": "https://kaneo.example.com/api/", "KANEO_TOKEN": "tok"}


def test_service_mode_defaults():
    s = load_settings(BASE)
    assert s.kaneo_base_url == "https://kaneo.example.com/api"
    assert s.kaneo_public_url == "https://kaneo.example.com"
    assert s.auth_mode is AuthMode.SERVICE
    assert s.device_client_id == "kaneo-templates"
    assert s.templates_dir == Path("/data/templates")
    assert s.app_lang == "fr"
    assert s.root_path == ""
    assert s.log_level == "info"
    assert not s.basic_auth_enabled


def test_missing_base_url_and_token_are_reported_together():
    with pytest.raises(ConfigError) as exc:
        load_settings({})
    assert len(exc.value.errors) == 2
    assert "KANEO_BASE_URL" in exc.value.errors[0]
    assert "KANEO_TOKEN" in exc.value.errors[1]


def test_invalid_base_url():
    with pytest.raises(ConfigError, match="http"):
        load_settings({**BASE, "KANEO_BASE_URL": "kaneo.example.com"})


def test_user_mode_requires_secret_key():
    env = {"KANEO_BASE_URL": "https://kaneo.example.com/api", "AUTH_MODE": "user"}
    with pytest.raises(ConfigError, match="SECRET_KEY is required"):
        load_settings(env)
    with pytest.raises(ConfigError, match="at least 32"):
        load_settings({**env, "SECRET_KEY": "short"})
    s = load_settings({**env, "SECRET_KEY": "x" * 32})
    assert s.auth_mode is AuthMode.USER
    assert s.kaneo_token is None


def test_unknown_auth_mode():
    with pytest.raises(ConfigError, match="AUTH_MODE"):
        load_settings({**BASE, "AUTH_MODE": "oidc"})


def test_basic_auth_needs_both_values():
    with pytest.raises(ConfigError, match="BASIC_AUTH"):
        load_settings({**BASE, "BASIC_AUTH_USER": "admin"})
    s = load_settings({**BASE, "BASIC_AUTH_USER": "admin", "BASIC_AUTH_PASSWORD": "pw"})
    assert s.basic_auth_enabled


def test_optional_values():
    s = load_settings(
        {
            **BASE,
            "KANEO_PUBLIC_URL": "https://board.example.com/",
            "ROOT_PATH": "templates/",
            "APP_LANG": "EN",
            "LOG_LEVEL": "DEBUG",
            "TEMPLATES_DIR": "/srv/tpl",
            "KANEO_WORKSPACE_ID": "ws1",
            "DEVICE_CLIENT_ID": "my-client",
        }
    )
    assert s.kaneo_public_url == "https://board.example.com"
    assert s.root_path == "/templates"
    assert s.app_lang == "en"
    assert s.log_level == "debug"
    assert s.templates_dir == Path("/srv/tpl")
    assert s.kaneo_workspace_id == "ws1"
    assert s.device_client_id == "my-client"


def test_invalid_lang_and_log_level():
    with pytest.raises(ConfigError) as exc:
        load_settings({**BASE, "APP_LANG": "de", "LOG_LEVEL": "verbose"})
    assert len(exc.value.errors) == 2


def test_repr_hides_secrets():
    s = load_settings(
        {**BASE, "KANEO_TOKEN": "secret-token", "BASIC_AUTH_USER": "u", "BASIC_AUTH_PASSWORD": "pw"}
    )
    assert "secret-token" not in repr(s)
    assert "pw" not in repr(s)


def test_app_refuses_to_start_without_config(monkeypatch, capsys):
    monkeypatch.delenv("KANEO_BASE_URL", raising=False)
    monkeypatch.delenv("KANEO_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        create_app()
    assert exc.value.code == 1
    assert "KANEO_BASE_URL is required" in capsys.readouterr().err


def test_healthz():
    client = TestClient(create_app(load_settings(BASE)))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_holidays_setting():
    assert load_settings(BASE).holidays is None
    assert load_settings({**BASE, "HOLIDAYS": "FR"}).holidays == "fr"
    assert load_settings({**BASE, "HOLIDAYS": "fr-alsace-moselle"}).holidays == "fr-alsace-moselle"
    with pytest.raises(ConfigError, match="HOLIDAYS must be one of"):
        load_settings({**BASE, "HOLIDAYS": "be"})
