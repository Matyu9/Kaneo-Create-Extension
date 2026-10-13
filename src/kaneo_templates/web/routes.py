from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from kaneo_templates import auth
from kaneo_templates.config import AuthMode, Settings
from kaneo_templates.creator import (
    DEFAULT_LABEL_COLOR,
    Creator,
    build_plan,
    plan_board,
    project_key,
)
from kaneo_templates.holidays import HolidayCalendar
from kaneo_templates.i18n import translator
from kaneo_templates.kaneo_client import KaneoAuthError, KaneoClient, KaneoError
from kaneo_templates.loader import LoadResult, load_templates
from kaneo_templates.schema import DateFieldSpec

logger = logging.getLogger(__name__)

LAST_CHOICE_COOKIE = "kt_last"
LAST_CHOICE_MAX_AGE = 365 * 24 * 3600
MAX_NAME_LENGTH = 200
MAX_TOKEN_LENGTH = 4096
# RFC 8628 §3.5: add 5 seconds to the polling interval on `slow_down`.
SLOW_DOWN_STEP = 5

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _user_mode(request: Request) -> bool:
    return _settings(request).auth_mode is AuthMode.USER


def _client(request: Request, token: str | None) -> KaneoClient:
    settings = _settings(request)
    factory = getattr(request.app.state, "client_factory", None)
    if factory is not None:
        return factory(settings, token)
    return KaneoClient(settings.kaneo_base_url, token)


def _templates(request: Request) -> LoadResult:
    return load_templates(_settings(request).templates_dir)


def _render(request: Request, name: str, **context) -> HTMLResponse:
    settings = _settings(request)
    context.setdefault("t", translator(settings.app_lang))
    context.setdefault("lang", settings.app_lang)
    context.setdefault("settings", settings)
    context.setdefault("user_mode", settings.auth_mode is AuthMode.USER)
    return templates.TemplateResponse(request, name, context)


def _page(request: Request, name: str, **context) -> HTMLResponse:
    """Full page: carries the CSRF token in the form and in its cookie."""
    csrf = auth.csrf_token(request)
    response = _render(request, name, csrf=csrf, **context)
    auth.set_csrf_cookie(request, response, csrf)
    return response


def _refresh() -> Response:
    """Ask htmx to reload the page, which shows the sign-in page when needed."""
    return Response(status_code=200, headers={"HX-Refresh": "true"})


def _signed_out(request: Request) -> Response:
    response = _refresh()
    auth.clear_session(request, response)
    return response


def _plan_options(request: Request) -> dict:
    settings = _settings(request)
    return {
        "date_format": translator(settings.app_lang)("date_format"),
        "holidays": HolidayCalendar(settings.holidays),
    }


def _last_choice(request: Request) -> tuple[str | None, str | None]:
    """Workspace and template of the last creation, as remembered by this browser."""
    try:
        raw = base64.urlsafe_b64decode(request.cookies.get(LAST_CHOICE_COOKIE, ""))
        data = json.loads(raw)
    except (binascii.Error, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    workspace, template = data.get("workspace"), data.get("template")
    return (
        workspace if isinstance(workspace, str) else None,
        template if isinstance(template, str) else None,
    )


def _remember_choice(request: Request, response: Response, workspace: str, template: str) -> None:
    response.set_cookie(
        LAST_CHOICE_COOKIE,
        # base64url keeps the value free of characters that cookies would quote.
        base64.urlsafe_b64encode(
            json.dumps({"workspace": workspace, "template": template}).encode()
        ).decode(),
        max_age=LAST_CHOICE_MAX_AGE,
        path=_settings(request).root_path or "/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


async def _current_user(kaneo: KaneoClient):
    # Only decorates the header: a failure must not hide the page.
    try:
        return await kaneo.current_user()
    except KaneoError as exc:
        logger.info("Cannot read the Kaneo session (%s %s)", exc.status, exc.code)
        return None


def _read_date(
    raw: str, spec: DateFieldSpec | None, errors: list[str], missing: str, invalid: str
) -> date | None:
    """Validate a date field of the form against its template section."""
    if spec is None:
        return None
    value = _parse_date(raw)
    if raw and value is None:
        errors.append(invalid)
    elif value is None and spec.required:
        errors.append(missing)
    return value


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    token = auth.kaneo_token(request)
    if _user_mode(request) and token is None:
        return _page(request, "login.html")

    workspaces = []
    kaneo_error = None
    token_rejected = False
    user = None
    async with _client(request, token) as kaneo:
        try:
            workspaces = await kaneo.list_workspaces()
            if _user_mode(request):
                user = await _current_user(kaneo)
        except KaneoAuthError:
            if _user_mode(request):
                response = _page(request, "login.html", notice="session_expired")
                auth.clear_session(request, response)
                return response
            token_rejected = True
        except KaneoError as exc:
            kaneo_error = exc.message

    loaded = _templates(request)
    template_ids = sorted(loaded.templates)
    last_workspace, last_template = _last_choice(request)
    selected_id = (
        last_template if last_template in loaded.templates else next(iter(template_ids), None)
    )
    workspace_ids = {w.id for w in workspaces}
    selected_workspace = (
        last_workspace if last_workspace in workspace_ids else _settings(request).kaneo_workspace_id
    )
    return _page(
        request,
        "index.html",
        signed_in=token is not None,
        user=user,
        workspaces=workspaces,
        kaneo_error=kaneo_error,
        token_rejected=token_rejected,
        loaded=loaded,
        template_ids=template_ids,
        selected_id=selected_id,
        selected=loaded.get(selected_id) if selected_id else None,
        selected_workspace=selected_workspace,
        end_date="",
        start_date="",
    )


@router.get("/template-fields", response_class=HTMLResponse)
async def template_fields(
    request: Request,
    template: Annotated[str, Query()] = "",
    end_date: Annotated[str, Query()] = "",
    start_date: Annotated[str, Query()] = "",
) -> Response:
    if _user_mode(request) and auth.kaneo_token(request) is None:
        return _refresh()
    selected = _templates(request).get(template)
    response = _render(
        request,
        "_template_fields.html",
        selected=selected,
        end_date=end_date,
        start_date=start_date,
    )
    response.headers["HX-Trigger"] = "refresh-preview"
    return response


@router.get("/preview", response_class=HTMLResponse)
async def preview(
    request: Request,
    template: Annotated[str, Query()] = "",
    end_date: Annotated[str, Query()] = "",
    start_date: Annotated[str, Query()] = "",
    name: Annotated[str, Query()] = "",
) -> Response:
    if _user_mode(request) and auth.kaneo_token(request) is None:
        return _refresh()
    selected = _templates(request).get(template)
    if selected is None:
        return _render(request, "_preview.html", plan=None)
    end = _parse_date(end_date) if selected.end_date else None
    start = _parse_date(start_date) if selected.start_date else None
    name = name.strip()[:MAX_NAME_LENGTH]
    plan = build_plan(
        selected,
        end,
        date.today(),
        start_date=start,
        project_name=name,
        **_plan_options(request),
    )
    return _render(
        request,
        "_preview.html",
        plan=plan,
        board=plan_board(plan),
        key=project_key(name),
        template=selected,
        default_label_color=DEFAULT_LABEL_COLOR,
    )


@router.post("/create", response_class=HTMLResponse)
async def create(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    workspace_id: Annotated[str, Form()] = "",
    template: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    end_date: Annotated[str, Form()] = "",
    start_date: Annotated[str, Form()] = "",
) -> Response:
    settings = _settings(request)
    if not auth.csrf_ok(request, csrf_token):
        return _render(request, "_result.html", errors=["error_csrf"], report=None)
    token = auth.kaneo_token(request)
    if _user_mode(request) and token is None:
        return _refresh()

    errors: list[str] = []
    selected = _templates(request).get(template)
    if selected is None:
        errors.append("error_template")
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        errors.append("error_name")
    end = start = None
    if selected is not None:
        end = _read_date(end_date, selected.end_date, errors, "error_date", "error_date_format")
        start = _read_date(
            start_date, selected.start_date, errors, "error_start_date", "error_start_date_format"
        )

    async with _client(request, token) as kaneo:
        try:
            workspaces = await kaneo.list_workspaces()
        except KaneoAuthError:
            if _user_mode(request):
                return _signed_out(request)
            return _render(request, "_result.html", errors=["token_rejected"], report=None)
        except KaneoError as exc:
            return _render(
                request,
                "_result.html",
                errors=["kaneo_unreachable"],
                detail=exc.message,
                report=None,
            )
        if workspace_id not in {w.id for w in workspaces}:
            errors.append("error_workspace")
        if errors or selected is None:
            return _render(request, "_result.html", errors=errors, report=None)

        plan = build_plan(
            selected,
            end,
            date.today(),
            start_date=start,
            project_name=name,
            **_plan_options(request),
        )
        report = await Creator(kaneo, settings.kaneo_public_url).run(
            workspace_id, selected, name, plan
        )

    logger.info(
        "Template %s: project %s, %d tasks, %d subtasks, %d failures",
        template,
        report.project_key or "-",
        report.tasks_created,
        report.subtasks_created,
        len(report.failures),
    )
    deletion = None
    if not report.ok and report.project_id:
        issued, signature = auth.sign_deletion(request, report.project_id)
        deletion = {"project_id": report.project_id, "issued": issued, "signature": signature}
    response = _render(
        request, "_result.html", errors=[], report=report, deletion=deletion, csrf=csrf_token
    )
    _remember_choice(request, response, workspace_id, template)
    if report.auth_failed and _user_mode(request):
        # Keep the report on screen; the next request goes to the sign-in page.
        auth.clear_session(request, response)
    return response


@router.post("/project/delete", response_class=HTMLResponse)
async def delete_incomplete_project(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    project_id: Annotated[str, Form()] = "",
    issued: Annotated[int, Form()] = 0,
    signature: Annotated[str, Form()] = "",
) -> Response:
    """Delete a project left incomplete by a creation, from the button of its report."""
    if not auth.csrf_ok(request, csrf_token):
        return _render(request, "_deleted.html", error="error_csrf")
    if not auth.deletion_allowed(request, project_id, issued, signature):
        return _render(request, "_deleted.html", error="delete_not_allowed")
    token = auth.kaneo_token(request)
    if _user_mode(request) and token is None:
        return _refresh()
    async with _client(request, token) as kaneo:
        try:
            await kaneo.delete_project(project_id)
        except KaneoAuthError:
            if _user_mode(request):
                return _signed_out(request)
            return _render(request, "_deleted.html", error="token_rejected")
        except KaneoError as exc:
            return _render(request, "_deleted.html", error="kaneo_error", detail=exc.message)
    logger.info("Incomplete project deleted on request")
    return _render(request, "_deleted.html", error=None)


# Sign-in (AUTH_MODE=user)


def _require_user_mode(request: Request) -> None:
    if not _user_mode(request):
        raise HTTPException(status_code=404)


@router.post("/login/device", response_class=HTMLResponse)
async def login_device(request: Request, csrf_token: Annotated[str, Form()] = "") -> Response:
    _require_user_mode(request)
    if not auth.csrf_ok(request, csrf_token):
        return _render(request, "_login_error.html", error="error_csrf")
    client_id = _settings(request).device_client_id
    async with _client(request, None) as kaneo:
        try:
            code = await kaneo.request_device_code(client_id)
        except KaneoError as exc:
            if exc.code == "invalid_client":
                return _render(request, "_login_error.html", error="device_not_allowed")
            logger.warning("Device code request failed (%s %s)", exc.status, exc.code)
            return _render(request, "_login_error.html", error="kaneo_error", detail=exc.message)
    state = auth.DeviceState(
        device_code=code.device_code,
        user_code=code.user_code,
        verification_uri=code.verification_uri_complete or code.verification_uri,
        interval=max(code.interval, 1),
        expires_at=time.time() + code.expires_in,
    )
    response = _render(request, "_device.html", state=state, csrf=csrf_token)
    auth.set_device_state(request, response, state)
    return response


@router.post("/login/device/poll", response_class=HTMLResponse)
async def login_device_poll(request: Request, csrf_token: Annotated[str, Form()] = "") -> Response:
    _require_user_mode(request)
    if not auth.csrf_ok(request, csrf_token):
        return _render(request, "_login_error.html", error="error_csrf")
    state = auth.device_state(request)
    if state is None or state.expired:
        response = _render(request, "_device_end.html", error="device_expired", csrf=csrf_token)
        auth.clear_device_state(request, response)
        return response

    async with _client(request, None) as kaneo:
        try:
            poll = await kaneo.poll_device_token(
                _settings(request).device_client_id, state.device_code
            )
        except KaneoError as exc:
            logger.warning("Device token poll failed (%s %s)", exc.status, exc.code)
            response = _render(
                request,
                "_device_end.html",
                error="kaneo_error",
                detail=exc.message,
                csrf=csrf_token,
            )
            auth.clear_device_state(request, response)
            return response

    if poll.status == "approved" and poll.access_token:
        response = _refresh()
        auth.set_session(request, response, poll.access_token)
        auth.clear_device_state(request, response)
        return response
    if poll.status in ("denied", "expired"):
        error = "device_denied" if poll.status == "denied" else "device_expired"
        response = _render(request, "_device_end.html", error=error, csrf=csrf_token)
        auth.clear_device_state(request, response)
        return response

    if poll.status == "slow_down":
        state.interval += SLOW_DOWN_STEP
    response = _render(request, "_device.html", state=state, csrf=csrf_token)
    auth.set_device_state(request, response, state)
    return response


@router.post("/login/token", response_class=HTMLResponse)
async def login_token(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    token: Annotated[str, Form()] = "",
) -> Response:
    _require_user_mode(request)
    if not auth.csrf_ok(request, csrf_token):
        return _render(request, "_login_error.html", error="error_csrf")
    token = token.strip()
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return _render(request, "_login_error.html", error="token_empty")
    async with _client(request, token) as kaneo:
        try:
            await kaneo.validate_token()
        except KaneoAuthError:
            return _render(request, "_login_error.html", error="token_invalid")
        except KaneoError as exc:
            return _render(request, "_login_error.html", error="kaneo_error", detail=exc.message)
    response = _refresh()
    auth.set_session(request, response, token)
    return response


@router.post("/logout")
async def logout(request: Request, csrf_token: Annotated[str, Form()] = "") -> Response:
    _require_user_mode(request)
    if not auth.csrf_ok(request, csrf_token):
        return _render(request, "_login_error.html", error="error_csrf")
    return _signed_out(request)
