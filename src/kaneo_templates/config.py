from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

MIN_SECRET_KEY_LENGTH = 32
LOG_LEVELS = ("critical", "error", "warning", "info", "debug")
LANGS = ("fr", "en")


class AuthMode(StrEnum):
    SERVICE = "service"
    USER = "user"


class ConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass(frozen=True)
class Settings:
    kaneo_base_url: str
    kaneo_public_url: str
    auth_mode: AuthMode
    kaneo_token: str | None
    secret_key: str | None
    device_client_id: str
    kaneo_workspace_id: str | None
    templates_dir: Path
    app_lang: str
    root_path: str
    basic_auth_user: str | None
    basic_auth_password: str | None
    log_level: str

    @property
    def basic_auth_enabled(self) -> bool:
        return self.basic_auth_user is not None

    def __repr__(self) -> str:
        # Keep secrets out of logs and tracebacks.
        return (
            f"Settings(kaneo_base_url={self.kaneo_base_url!r}, auth_mode={self.auth_mode.value!r}, "
            f"templates_dir={str(self.templates_dir)!r}, app_lang={self.app_lang!r}, "
            f"root_path={self.root_path!r}, basic_auth_enabled={self.basic_auth_enabled})"
        )


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _valid_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _derive_public_url(base_url: str) -> str:
    return base_url.removesuffix("/api")


def _normalize_root_path(value: str | None) -> str:
    if not value:
        return ""
    return "/" + value.strip("/")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read and validate the configuration, collecting every error before failing."""
    env = os.environ if env is None else env
    errors: list[str] = []

    base_url = _get(env, "KANEO_BASE_URL")
    if base_url is None:
        errors.append("KANEO_BASE_URL is required (Kaneo API URL, ending with /api).")
    elif not _valid_http_url(base_url):
        errors.append("KANEO_BASE_URL must be an http(s) URL.")
    else:
        base_url = base_url.rstrip("/")

    public_url = _get(env, "KANEO_PUBLIC_URL")
    if public_url is not None:
        if _valid_http_url(public_url):
            public_url = public_url.rstrip("/")
        else:
            errors.append("KANEO_PUBLIC_URL must be an http(s) URL.")

    raw_mode = (_get(env, "AUTH_MODE") or AuthMode.SERVICE.value).lower()
    try:
        auth_mode = AuthMode(raw_mode)
    except ValueError:
        errors.append("AUTH_MODE must be 'service' or 'user'.")
        auth_mode = AuthMode.SERVICE

    kaneo_token = _get(env, "KANEO_TOKEN")
    if auth_mode is AuthMode.SERVICE and kaneo_token is None:
        errors.append("KANEO_TOKEN is required when AUTH_MODE=service.")

    secret_key = _get(env, "SECRET_KEY")
    if auth_mode is AuthMode.USER:
        if secret_key is None:
            errors.append("SECRET_KEY is required when AUTH_MODE=user.")
        elif len(secret_key) < MIN_SECRET_KEY_LENGTH:
            errors.append(f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters long.")

    device_client_id = _get(env, "DEVICE_CLIENT_ID") or "kaneo-templates"

    app_lang = (_get(env, "APP_LANG") or "fr").lower()
    if app_lang not in LANGS:
        errors.append(f"APP_LANG must be one of: {', '.join(LANGS)}.")

    log_level = (_get(env, "LOG_LEVEL") or "info").lower()
    if log_level not in LOG_LEVELS:
        errors.append(f"LOG_LEVEL must be one of: {', '.join(LOG_LEVELS)}.")

    basic_user = _get(env, "BASIC_AUTH_USER")
    basic_password = _get(env, "BASIC_AUTH_PASSWORD")
    if (basic_user is None) != (basic_password is None):
        errors.append("BASIC_AUTH_USER and BASIC_AUTH_PASSWORD must be set together.")

    if errors or base_url is None:
        raise ConfigError(errors)

    return Settings(
        kaneo_base_url=base_url,
        kaneo_public_url=public_url or _derive_public_url(base_url),
        auth_mode=auth_mode,
        kaneo_token=kaneo_token,
        secret_key=secret_key,
        device_client_id=device_client_id,
        kaneo_workspace_id=_get(env, "KANEO_WORKSPACE_ID"),
        templates_dir=Path(_get(env, "TEMPLATES_DIR") or "/data/templates"),
        app_lang=app_lang,
        root_path=_normalize_root_path(_get(env, "ROOT_PATH")),
        basic_auth_user=basic_user,
        basic_auth_password=basic_password,
        log_level=log_level,
    )
