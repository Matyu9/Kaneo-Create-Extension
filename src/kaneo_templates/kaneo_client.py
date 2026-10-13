"""Async client for the subset of the Kaneo API used by this module.

See docs/kaneo-api-notes.md for the endpoints and their behaviour.

Retry policy (SPEC §7): reads are retried once on network errors and 5xx; writes are
never retried, except after a 429 which means the request was not processed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel

from kaneo_templates.scheduling import to_kaneo_datetime

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
MAX_RATE_LIMIT_WAITS = 5
MAX_RETRY_AFTER = 60.0
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_PROJECT_ICON = "Layout"


class KaneoError(Exception):
    """Error returned by Kaneo or raised while talking to it.

    `status` is None for network errors and unexpected responses.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.issues = issues or []

    def __str__(self) -> str:
        prefix = f"{self.status} {self.code}" if self.status else (self.code or "error")
        return f"Kaneo {prefix}: {self.message}"


class KaneoAuthError(KaneoError):
    """Kaneo rejected the credential (401 UNAUTHORIZED): the user must sign in again."""


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Workspace(_Model):
    id: str
    name: str
    slug: str | None = None


class User(_Model):
    id: str
    name: str | None = None
    email: str | None = None

    @property
    def initials(self) -> str:
        words = (self.name or self.email or "?").split()
        return "".join(w[0] for w in words[:2]).upper()


class Project(_Model):
    id: str
    workspace_id: str | None = None
    name: str
    slug: str


class Column(_Model):
    id: str
    name: str
    slug: str
    position: float = 0
    is_final: bool = False


class Task(_Model):
    id: str
    title: str
    status: str
    number: int | None = None
    due_date: str | None = None


class Label(_Model):
    id: str
    name: str
    color: str
    task_id: str | None = None
    workspace_id: str | None = None

    @property
    def is_definition(self) -> bool:
        """Workspace-level label, as opposed to a copy bound to a task."""
        return self.task_id is None


class TaskRelation(_Model):
    id: str
    source_task_id: str
    target_task_id: str
    relation_type: str


class DeviceCode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int


@dataclass(frozen=True)
class DevicePoll:
    status: Literal["approved", "pending", "slow_down", "denied", "expired"]
    access_token: str | None = None
    expires_in: int | None = None


T = TypeVar("T")
Sleep = Callable[[float], Awaitable[None]]


class KaneoClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._sleep = sleep

    async def __aenter__(self) -> KaneoClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # Reads

    async def list_workspaces(self) -> list[Workspace]:
        return self._parse(list[Workspace], await self._read("GET", "workspace"))

    async def validate_token(self) -> list[Workspace]:
        """Check the credential; works for API keys and session tokens alike."""
        return await self.list_workspaces()

    async def current_user(self) -> User | None:
        """The user behind a session token; None for an API key, which has no session."""
        try:
            data = await self._read("GET", "auth/get-session")
        except KaneoAuthError:
            return None
        user = data.get("user") if isinstance(data, dict) else None
        return self._parse(User, user) if isinstance(user, dict) else None

    async def list_projects(self, workspace_id: str) -> list[Project]:
        data = await self._read(
            "GET", "project", params={"workspaceId": workspace_id, "includeArchived": "true"}
        )
        return self._parse(list[Project], data)

    async def list_columns(self, project_id: str) -> list[Column]:
        columns = self._parse(list[Column], await self._read("GET", f"column/{project_id}"))
        return sorted(columns, key=lambda c: c.position)

    async def list_workspace_labels(self, workspace_id: str) -> list[Label]:
        data = await self._read("GET", f"label/workspace/{workspace_id}")
        return self._parse(list[Label], data)

    async def list_task_relations(self, task_id: str) -> list[TaskRelation]:
        data = await self._read("GET", f"task-relation/{task_id}")
        return self._parse(list[TaskRelation], data)

    # Writes

    async def create_project(
        self, workspace_id: str, name: str, slug: str, icon: str = DEFAULT_PROJECT_ICON
    ) -> Project:
        body = {"workspaceId": workspace_id, "name": name, "slug": slug, "icon": icon}
        return self._parse(Project, await self._write("POST", "project", json=body))

    async def delete_project(self, project_id: str) -> None:
        await self._write("DELETE", f"project/{project_id}")

    async def create_column(self, project_id: str, name: str, *, is_final: bool = False) -> Column:
        body = {"name": name, "isFinal": is_final}
        return self._parse(Column, await self._write("POST", f"column/{project_id}", json=body))

    async def update_column(
        self, column_id: str, *, name: str | None = None, is_final: bool | None = None
    ) -> Column:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if is_final is not None:
            body["isFinal"] = is_final
        return self._parse(Column, await self._write("PUT", f"column/{column_id}", json=body))

    async def delete_column(self, column_id: str) -> None:
        await self._write("DELETE", f"column/{column_id}")

    async def reorder_columns(self, project_id: str, column_ids: list[str]) -> None:
        body = {"columns": [{"id": cid, "position": i} for i, cid in enumerate(column_ids)]}
        await self._write("PUT", f"column/reorder/{project_id}", json=body)

    async def create_task(
        self,
        project_id: str,
        *,
        title: str,
        status: str,
        description: str | None = None,
        priority: str | None = None,
        due_date: date | None = None,
    ) -> Task:
        body: dict[str, Any] = {
            "title": title,
            "description": description or "",
            "priority": priority or "no-priority",
            "status": status,
        }
        if due_date is not None:
            body["dueDate"] = to_kaneo_datetime(due_date)
        return self._parse(Task, await self._write("POST", f"task/{project_id}", json=body))

    async def create_label(self, workspace_id: str, name: str, color: str) -> Label:
        """Create a workspace label definition (returns the existing one if the name exists)."""
        body = {"workspaceId": workspace_id, "name": name, "color": color}
        return self._parse(Label, await self._write("POST", "label", json=body))

    async def attach_label(self, label_id: str, task_id: str) -> Label:
        """Attach a label definition to a task.

        Only pass a definition id: Kaneo moves a task-bound label to the new task instead
        of copying it.
        """
        data = await self._write("PUT", f"label/{label_id}/task", json={"taskId": task_id})
        return self._parse(Label, data)

    async def delete_label(self, label_id: str) -> None:
        await self._write("DELETE", f"label/{label_id}")

    async def link_subtask(self, parent_task_id: str, subtask_id: str) -> TaskRelation:
        body = {
            "sourceTaskId": parent_task_id,
            "targetTaskId": subtask_id,
            "relationType": "subtask",
        }
        return self._parse(TaskRelation, await self._write("POST", "task-relation", json=body))

    # Device flow (RFC 8628). These endpoints do not need a token.

    async def request_device_code(self, client_id: str) -> DeviceCode:
        response = await self._send("POST", "auth/device/code", json={"client_id": client_id})
        data = _json_or_none(response)
        if response.is_success:
            return self._parse(DeviceCode, data)
        error = data.get("error") if isinstance(data, dict) else None
        description = data.get("error_description") if isinstance(data, dict) else None
        if error == "invalid_client":
            raise KaneoError(
                f"client_id {client_id!r} is not allowed by this Kaneo instance "
                "(DEVICE_AUTH_CLIENT_IDS)",
                status=response.status_code,
                code=error,
            )
        raise KaneoError(
            description or error or _error_text(response),
            status=response.status_code,
            code=error,
        )

    async def poll_device_token(self, client_id: str, device_code: str) -> DevicePoll:
        body = {"grant_type": DEVICE_GRANT_TYPE, "device_code": device_code, "client_id": client_id}
        response = await self._send("POST", "auth/device/token", json=body)
        data = _json_or_none(response)
        if not isinstance(data, dict):
            raise KaneoError(_error_text(response), status=response.status_code)
        token = data.get("access_token")
        if response.is_success and isinstance(token, str) and token:
            expires_in = data.get("expires_in")
            return DevicePoll(
                "approved", token, expires_in if isinstance(expires_in, int) else None
            )
        error = data.get("error")
        status = _DEVICE_ERRORS.get(error) if isinstance(error, str) else None
        if status is not None:
            return DevicePoll(status)
        raise KaneoError(
            str(data.get("error_description") or error or "unexpected device token response"),
            status=response.status_code,
            code=error if isinstance(error, str) else None,
        )

    # Transport

    async def _read(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._send(method, path, **kwargs)
            if response.status_code < 500:
                return self._handle(response)
            logger.info(
                "Kaneo %s %s returned %s, retrying once", method, path, response.status_code
            )
        except KaneoError as exc:
            if exc.status is not None:
                raise
            logger.info("Kaneo %s %s failed (%s), retrying once", method, path, exc.message)
        return self._handle(await self._send(method, path, **kwargs))

    async def _write(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._handle(await self._send(method, path, **kwargs))

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(MAX_RATE_LIMIT_WAITS + 1):
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.TimeoutException:
                raise KaneoError("request to Kaneo timed out", code="TIMEOUT") from None
            except httpx.HTTPError as exc:
                raise KaneoError(
                    f"cannot reach Kaneo ({type(exc).__name__})", code="NETWORK_ERROR"
                ) from None
            if response.status_code != 429 or attempt == MAX_RATE_LIMIT_WAITS:
                return response
            wait = _retry_after(response)
            logger.info("Kaneo rate limit reached, waiting %.0f s", wait)
            await self._sleep(wait)
        return response

    def _handle(self, response: httpx.Response) -> Any:
        if response.is_success:
            if not response.content:
                return None
            data = _json_or_none(response)
            if data is None:
                raise KaneoError("Kaneo returned a non-JSON response", status=response.status_code)
            return data
        data = _json_or_none(response)
        message = _error_text(response)
        code = None
        issues = None
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("error") or message)
            code = data.get("code") if isinstance(data.get("code"), str) else None
            issues = data.get("issues") if isinstance(data.get("issues"), list) else None
        cls = (
            KaneoAuthError
            if response.status_code == 401 and code in (None, "UNAUTHORIZED")
            else KaneoError
        )
        raise cls(message, status=response.status_code, code=code, issues=issues)

    @staticmethod
    def _parse(model: type[T] | Any, data: Any) -> T:
        try:
            return TypeAdapter(model).validate_python(data)
        except ValidationError:
            raise KaneoError("unexpected response format from Kaneo") from None


_DEVICE_ERRORS: dict[str, Literal["pending", "slow_down", "denied", "expired"]] = {
    "authorization_pending": "pending",
    "slow_down": "slow_down",
    "access_denied": "denied",
    "expired_token": "expired",
}


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_text(response: httpx.Response) -> str:
    # Older Kaneo versions answer some errors in plain text.
    text = response.text.strip()
    if text and not text.startswith("<"):
        return text[:300]
    return response.reason_phrase or f"HTTP {response.status_code}"


def _retry_after(response: httpx.Response) -> float:
    try:
        value = float(response.headers.get("Retry-After", "1"))
    except ValueError:
        value = 1.0
    return min(max(value, 0.0), MAX_RETRY_AFTER)
