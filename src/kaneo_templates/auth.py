"""Security around the web UI.

- HTTP Basic Auth (optional) and security headers, for every request.
- CSRF double-submit token for every POST (docs/decisions.md D16).
- `user` mode: the Kaneo token of each user lives in an `HttpOnly`, `Secure` cookie encrypted
  with Fernet, under a key derived from `SECRET_KEY` (SPEC §4, decisions D13 and D18).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from kaneo_templates.config import AuthMode, Settings

PUBLIC_PATHS = ("/healthz",)

CSRF_COOKIE = "kt_csrf"
SESSION_COOKIE = "kt_session"
DEVICE_COOKIE = "kt_device"
# Kaneo sessions slide by 7 days on use; the cookie outlives them and a 401 clears it.
SESSION_MAX_AGE = 30 * 24 * 3600

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
    # Signs the "delete this incomplete project" buttons; a restart invalidates them.
    app.state.signing_key = secrets.token_bytes(32)
    if settings.auth_mode is AuthMode.USER and settings.secret_key is not None:
        app.state.cipher = CookieCipher(settings.secret_key)

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


# Encrypted cookies


class CookieCipher:
    """Fernet encryption of small JSON payloads, keyed from `SECRET_KEY` with HKDF."""

    def __init__(self, secret_key: str) -> None:
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"kaneo-templates cookies"
        ).derive(secret_key.encode())
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def seal(self, data: dict) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def open(self, value: str | None, max_age: int) -> dict | None:
        """Decrypt a cookie; None when absent, tampered with, or older than `max_age` s."""
        if not value:
            return None
        try:
            data = json.loads(self._fernet.decrypt(value.encode(), ttl=max_age))
        except (InvalidToken, ValueError):
            return None
        return data if isinstance(data, dict) else None


@dataclass
class DeviceState:
    """Pending device flow, kept in its own encrypted cookie between two polls."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


def _cipher(request: Request) -> CookieCipher:
    return request.app.state.cipher


def _cookie_args(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    return {"path": settings.root_path or "/", "httponly": True, "samesite": "lax"}


def kaneo_token(request: Request) -> str | None:
    """The token to call Kaneo with: the service token, or the user's from their cookie."""
    settings: Settings = request.app.state.settings
    if settings.auth_mode is AuthMode.SERVICE:
        return settings.kaneo_token
    data = _cipher(request).open(request.cookies.get(SESSION_COOKIE), SESSION_MAX_AGE)
    token = data.get("token") if data else None
    return token if isinstance(token, str) and token else None


def set_session(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _cipher(request).seal({"token": token}),
        max_age=SESSION_MAX_AGE,
        secure=True,
        **_cookie_args(request),
    )


def clear_session(request: Request, response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, secure=True, **_cookie_args(request))


def device_state(request: Request) -> DeviceState | None:
    # The Fernet TTL is a backstop; `expires_at` carries Kaneo's own expiry.
    data = _cipher(request).open(request.cookies.get(DEVICE_COOKIE), 3600)
    if data is None:
        return None
    try:
        return DeviceState(**data)
    except TypeError:
        return None


def set_device_state(request: Request, response: Response, state: DeviceState) -> None:
    response.set_cookie(
        DEVICE_COOKIE,
        _cipher(request).seal(asdict(state)),
        max_age=max(int(state.expires_at - time.time()), 1),
        secure=True,
        **_cookie_args(request),
    )


def clear_device_state(request: Request, response: Response) -> None:
    response.delete_cookie(DEVICE_COOKIE, secure=True, **_cookie_args(request))


# CSRF (double submit)


def csrf_token(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)


def set_csrf_cookie(request: Request, response: Response, value: str) -> None:
    response.set_cookie(
        CSRF_COOKIE, value, secure=request.url.scheme == "https", **_cookie_args(request)
    )


def csrf_ok(request: Request, submitted: str) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return bool(cookie) and secrets.compare_digest(cookie, submitted)


# Signed actions


DELETION_TTL = 3600


def sign_deletion(request: Request, project_id: str, issued: int | None = None) -> tuple[int, str]:
    """Signature allowing the deletion of a project this app has just created."""
    issued = int(time.time()) if issued is None else issued
    message = f"delete:{project_id}:{issued}".encode()
    return issued, hmac.new(request.app.state.signing_key, message, hashlib.sha256).hexdigest()


def deletion_allowed(request: Request, project_id: str, issued: int, signature: str) -> bool:
    if not project_id or not 0 <= time.time() - issued <= DELETION_TTL:
        return False
    _, expected = sign_deletion(request, project_id, issued)
    return hmac.compare_digest(expected, signature)
