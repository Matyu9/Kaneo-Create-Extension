from __future__ import annotations

import logging
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from kaneo_templates.config import AuthMode, Settings
from kaneo_templates.creator import Creator, build_plan
from kaneo_templates.i18n import translator
from kaneo_templates.kaneo_client import KaneoAuthError, KaneoClient, KaneoError
from kaneo_templates.loader import LoadResult, load_templates

logger = logging.getLogger(__name__)

CSRF_COOKIE = "kt_csrf"
MAX_NAME_LENGTH = 200

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _client(request: Request) -> KaneoClient:
    settings = _settings(request)
    factory = getattr(request.app.state, "client_factory", None)
    if factory is not None:
        return factory(settings)
    return KaneoClient(settings.kaneo_base_url, settings.kaneo_token)


def _templates(request: Request) -> LoadResult:
    return load_templates(_settings(request).templates_dir)


def _render(request: Request, name: str, **context) -> HTMLResponse:
    settings = _settings(request)
    context.setdefault("t", translator(settings.app_lang))
    context.setdefault("lang", settings.app_lang)
    return templates.TemplateResponse(request, name, context)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    settings = _settings(request)
    loaded = _templates(request)
    workspaces = []
    kaneo_error = None
    token_rejected = False
    if settings.auth_mode is AuthMode.SERVICE:
        async with _client(request) as kaneo:
            try:
                workspaces = await kaneo.list_workspaces()
            except KaneoAuthError:
                token_rejected = True
            except KaneoError as exc:
                kaneo_error = exc.message

    csrf = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    first_id = next(iter(sorted(loaded.templates)), None)
    response = _render(
        request,
        "index.html",
        settings=settings,
        user_mode=settings.auth_mode is AuthMode.USER,
        workspaces=workspaces,
        kaneo_error=kaneo_error,
        token_rejected=token_rejected,
        loaded=loaded,
        template_ids=sorted(loaded.templates),
        selected_id=first_id,
        selected=loaded.get(first_id) if first_id else None,
        end_date="",
        csrf=csrf,
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path=settings.root_path or "/",
    )
    return response


@router.get("/template-fields", response_class=HTMLResponse)
async def template_fields(
    request: Request,
    template: Annotated[str, Query()] = "",
    end_date: Annotated[str, Query()] = "",
) -> Response:
    selected = _templates(request).get(template)
    response = _render(request, "_template_fields.html", selected=selected, end_date=end_date)
    response.headers["HX-Trigger"] = "refresh-preview"
    return response


@router.get("/preview", response_class=HTMLResponse)
async def preview(
    request: Request,
    template: Annotated[str, Query()] = "",
    end_date: Annotated[str, Query()] = "",
) -> Response:
    selected = _templates(request).get(template)
    if selected is None:
        return _render(request, "_preview.html", plan=None)
    end = _parse_date(end_date) if selected.end_date else None
    plan = build_plan(selected, end, date.today())
    return _render(request, "_preview.html", plan=plan, template=selected)


@router.post("/create", response_class=HTMLResponse)
async def create(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    workspace_id: Annotated[str, Form()] = "",
    template: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    end_date: Annotated[str, Form()] = "",
) -> Response:
    settings = _settings(request)
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not secrets.compare_digest(cookie, csrf_token):
        return _render(request, "_result.html", errors=["error_csrf"], report=None)
    if settings.auth_mode is not AuthMode.SERVICE:
        return _render(request, "_result.html", errors=["user_mode_pending"], report=None)

    errors: list[str] = []
    selected = _templates(request).get(template)
    if selected is None:
        errors.append("error_template")
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        errors.append("error_name")
    end: date | None = None
    if selected is not None and selected.end_date is not None:
        end = _parse_date(end_date)
        if end_date and end is None:
            errors.append("error_date_format")
        elif end is None and selected.end_date_required:
            errors.append("error_date")

    async with _client(request) as kaneo:
        try:
            workspaces = await kaneo.list_workspaces()
        except KaneoAuthError:
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

        plan = build_plan(selected, end, date.today())
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
    return _render(request, "_result.html", errors=[], report=report)
