"""HTTP Basic Auth (optional) and security headers.

The `user` mode (device flow, encrypted cookie) comes with step 6 of the spec.
"""

from __future__ import annotations

import base64
import binascii
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from kaneo_templates.config import Settings

PUBLIC_PATHS = ("/healthz",)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


def _basic_auth_ok(header: str | None, user: str, password: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    given_user, sep, given_password = decoded.partition(":")
    if not sep:
        return False
    # Evaluate both comparisons to keep timing independent of which part is wrong.
    user_ok = secrets.compare_digest(given_user.encode(), user.encode())
    password_ok = secrets.compare_digest(given_password.encode(), password.encode())
    return user_ok and password_ok


def install(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if (
            settings.basic_auth_user is not None
            and settings.basic_auth_password is not None
            and request.url.path not in PUBLIC_PATHS
            and not _basic_auth_ok(
                request.headers.get("Authorization"),
                settings.basic_auth_user,
                settings.basic_auth_password,
            )
        ):
            response = PlainTextResponse("Authentication required", status_code=401)
            response.headers["WWW-Authenticate"] = 'Basic realm="kaneo-templates", charset="UTF-8"'
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
