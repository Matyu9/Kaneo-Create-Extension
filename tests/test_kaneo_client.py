import json
from datetime import date

import httpx
import pytest
import respx

from kaneo_templates.kaneo_client import (
    MAX_RATE_LIMIT_WAITS,
    DevicePoll,
    KaneoAuthError,
    KaneoClient,
    KaneoError,
)

pytestmark = pytest.mark.anyio

BASE = "https://kaneo.test/api"
TOKEN = "test-token-value"

COLUMN = {
    "id": "c1",
    "projectId": "p1",
    "name": "To Do",
    "slug": "to-do",
    "position": 0,
    "icon": None,
    "color": None,
    "isFinal": False,
    "createdAt": "2026-01-01T00:00:00.000Z",
    "updatedAt": "2026-01-01T00:00:00.000Z",
}
PROJECT = {
    "id": "p1",
    "workspaceId": "w1",
    "slug": "EVT",
    "icon": "Layout",
    "name": "Event",
    "description": None,
    "createdAt": "2026-01-01T00:00:00.000Z",
    "isPublic": False,
    "archivedAt": None,
    "position": 0,
    "lastTaskNumber": 0,
    "backgroundVersion": None,
}
TASK = {
    "id": "t1",
    "projectId": "p1",
    "position": 0,
    "number": 1,
    "userId": None,
    "title": "Book the room",
    "description": "",
    "status": "to-do",
    "priority": "high",
    "startDate": None,
    "dueDate": "2026-05-16T12:00:00.000Z",
    "createdAt": "2026-01-01T00:00:00.000Z",
}
LABEL_DEF = {
    "id": "l1",
    "name": "logistics",
    "color": "#6b7280",
    "createdAt": "2026-01-01T00:00:00.000Z",
    "updatedAt": "2026-01-01T00:00:00.000Z",
    "taskId": None,
    "workspaceId": "w1",
}


class FakeSleep:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


@pytest.fixture
def sleep():
    return FakeSleep()


@pytest.fixture
async def client(sleep):
    async with KaneoClient(BASE + "/", TOKEN, sleep=sleep) as c:
        yield c


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        yield mock


def body(route, index=-1):
    return json.loads(route.calls[index].request.content)


def error(status, code, message="failed", **extra):
    return httpx.Response(status, json={"message": message, "code": code, **extra})


# Reads


async def test_list_workspaces_sends_bearer_token(client, api):
    route = api.get("/workspace").respond(
        json=[
            {
                "id": "w1",
                "name": "Main",
                "slug": "main",
                "logo": None,
                "description": None,
                "createdAt": "2026-01-01T00:00:00.000Z",
                "role": "owner",
            }
        ]
    )
    workspaces = await client.list_workspaces()
    assert [(w.id, w.name) for w in workspaces] == [("w1", "Main")]
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert str(request.url) == "https://kaneo.test/api/workspace"


async def test_list_projects_includes_archived(client, api):
    route = api.get("/project").respond(json=[PROJECT])
    projects = await client.list_projects("w1")
    assert projects[0].slug == "EVT"
    params = route.calls.last.request.url.params
    assert params["workspaceId"] == "w1"
    assert params["includeArchived"] == "true"


async def test_list_columns_sorted_by_position(client, api):
    api.get("/column/p1").respond(
        json=[
            {**COLUMN, "id": "c2", "slug": "done", "name": "Done", "position": 3, "isFinal": True},
            COLUMN,
        ]
    )
    columns = await client.list_columns("p1")
    assert [c.slug for c in columns] == ["to-do", "done"]
    assert columns[1].is_final


async def test_list_workspace_labels_distinguishes_definitions(client, api):
    api.get("/label/workspace/w1").respond(
        json=[LABEL_DEF, {**LABEL_DEF, "id": "l2", "taskId": "t9"}]
    )
    labels = await client.list_workspace_labels("w1")
    assert [label.is_definition for label in labels] == [True, False]


async def test_list_task_relations(client, api):
    api.get("/task-relation/t1").respond(
        json=[
            {
                "id": "r1",
                "sourceTaskId": "t1",
                "targetTaskId": "t2",
                "relationType": "subtask",
                "createdAt": "2026-01-01T00:00:00.000Z",
            }
        ]
    )
    relations = await client.list_task_relations("t1")
    assert relations[0].target_task_id == "t2"


async def test_validate_token_uses_workspace_list(client, api):
    route = api.get("/workspace").respond(json=[])
    assert await client.validate_token() == []
    assert route.called


# Writes


async def test_create_project(client, api):
    route = api.post("/project").respond(json=PROJECT)
    project = await client.create_project("w1", "Event", "EVT")
    assert project.id == "p1"
    assert body(route) == {"workspaceId": "w1", "name": "Event", "slug": "EVT", "icon": "Layout"}


async def test_delete_project(client, api):
    route = api.delete("/project/p1").respond(json=PROJECT)
    await client.delete_project("p1")
    assert route.called


async def test_column_writes(client, api):
    create = api.post("/column/p1").respond(
        json={**COLUMN, "id": "c9", "name": "Fait", "slug": "fait"}
    )
    update = api.put("/column/c1").respond(json={**COLUMN, "name": "To do"})
    delete = api.delete("/column/c2").respond(json=COLUMN)
    reorder = api.put("/column/reorder/p1").respond(json={"success": True})

    column = await client.create_column("p1", "Fait", is_final=True)
    assert column.slug == "fait"
    assert body(create) == {"name": "Fait", "isFinal": True}

    await client.update_column("c1", name="To do")
    assert body(update) == {"name": "To do"}

    await client.delete_column("c2")
    assert delete.called

    await client.reorder_columns("p1", ["c9", "c1"])
    assert body(reorder) == {"columns": [{"id": "c9", "position": 0}, {"id": "c1", "position": 1}]}


async def test_create_task_with_due_date(client, api):
    route = api.post("/task/p1").respond(json=TASK)
    task = await client.create_task(
        "p1",
        title="Book the room",
        status="to-do",
        description="Check access",
        priority="high",
        due_date=date(2026, 5, 16),
    )
    assert task.id == "t1"
    assert body(route) == {
        "title": "Book the room",
        "description": "Check access",
        "priority": "high",
        "status": "to-do",
        "dueDate": "2026-05-16T12:00:00.000Z",
    }


async def test_create_task_defaults(client, api):
    route = api.post("/task/p1").respond(json={**TASK, "dueDate": None, "priority": "no-priority"})
    await client.create_task("p1", title="T", status="to-do")
    assert body(route) == {
        "title": "T",
        "description": "",
        "priority": "no-priority",
        "status": "to-do",
    }


async def test_labels(client, api):
    create = api.post("/label").respond(json=LABEL_DEF)
    attach = api.put("/label/l1/task").respond(json={**LABEL_DEF, "id": "l7", "taskId": "t1"})
    label = await client.create_label("w1", "logistics", "#6b7280")
    assert label.is_definition
    assert body(create) == {"workspaceId": "w1", "name": "logistics", "color": "#6b7280"}
    bound = await client.attach_label("l1", "t1")
    assert bound.task_id == "t1"
    assert body(attach) == {"taskId": "t1"}


async def test_delete_label(client, api):
    route = api.delete("/label/l1").respond(json=LABEL_DEF)
    await client.delete_label("l1")
    assert route.called


async def test_link_subtask_parent_is_source(client, api):
    route = api.post("/task-relation").respond(
        json={
            "id": "r1",
            "sourceTaskId": "parent",
            "targetTaskId": "child",
            "relationType": "subtask",
            "createdAt": "2026-01-01T00:00:00.000Z",
        }
    )
    await client.link_subtask("parent", "child")
    assert body(route) == {
        "sourceTaskId": "parent",
        "targetTaskId": "child",
        "relationType": "subtask",
    }


# Errors


async def test_unauthorized_raises_auth_error(client, api):
    api.get("/workspace").mock(return_value=error(401, "UNAUTHORIZED", "Unauthorized"))
    with pytest.raises(KaneoAuthError) as exc:
        await client.list_workspaces()
    assert exc.value.status == 401
    assert TOKEN not in str(exc.value)


async def test_integration_401_is_not_an_auth_error(client, api):
    api.post("/task/p1").mock(return_value=error(401, "INTEGRATION_AUTH_FAILED"))
    with pytest.raises(KaneoError) as exc:
        await client.create_task("p1", title="T", status="to-do")
    assert not isinstance(exc.value, KaneoAuthError)
    assert exc.value.code == "INTEGRATION_AUTH_FAILED"


async def test_validation_error_details(client, api):
    issues = [{"path": "body.status", "message": "Invalid status"}]
    api.post("/task/p1").mock(
        return_value=error(400, "VALIDATION_ERROR", "status: Invalid status", issues=issues)
    )
    with pytest.raises(KaneoError) as exc:
        await client.create_task("p1", title="T", status="nope")
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.issues == issues
    assert exc.value.message == "status: Invalid status"


async def test_plain_text_error_from_older_kaneo(client, api):
    api.post("/project").respond(400, text="Project key already used")
    with pytest.raises(KaneoError) as exc:
        await client.create_project("w1", "X", "X")
    assert exc.value.message == "Project key already used"
    assert exc.value.code is None


async def test_html_error_page_is_not_echoed(client, api):
    api.get("/workspace").respond(502, html="<html>Bad gateway</html>")
    with pytest.raises(KaneoError) as exc:
        await client.list_workspaces()
    assert "<html>" not in exc.value.message


@pytest.mark.parametrize("status", [409, 500, 503])
async def test_writes_are_not_retried(client, api, status):
    route = api.post("/task/p1").mock(return_value=error(status, "X"))
    with pytest.raises(KaneoError):
        await client.create_task("p1", title="T", status="to-do")
    assert route.call_count == 1


async def test_write_network_error_not_retried(client, api):
    route = api.post("/project").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(KaneoError) as exc:
        await client.create_project("w1", "X", "X")
    assert exc.value.code == "NETWORK_ERROR"
    assert route.call_count == 1


async def test_read_retried_once_on_server_error(client, api):
    route = api.get("/workspace").mock(
        side_effect=[error(503, "SERVICE_UNAVAILABLE"), httpx.Response(200, json=[])]
    )
    assert await client.list_workspaces() == []
    assert route.call_count == 2


async def test_read_fails_after_one_retry(client, api):
    route = api.get("/workspace").mock(return_value=error(500, "INTERNAL_SERVER_ERROR"))
    with pytest.raises(KaneoError) as exc:
        await client.list_workspaces()
    assert exc.value.status == 500
    assert route.call_count == 2


async def test_read_retried_once_on_network_error(client, api):
    route = api.get("/column/p1").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=[COLUMN])]
    )
    assert len(await client.list_columns("p1")) == 1
    assert route.call_count == 2


async def test_read_not_retried_on_client_error(client, api):
    route = api.get("/column/p1").mock(return_value=error(404, "NOT_FOUND"))
    with pytest.raises(KaneoError):
        await client.list_columns("p1")
    assert route.call_count == 1


async def test_timeout(client, api):
    api.get("/workspace").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(KaneoError) as exc:
        await client.list_workspaces()
    assert exc.value.code == "TIMEOUT"


async def test_rate_limited_write_is_replayed_after_retry_after(client, api, sleep):
    limited = httpx.Response(
        429, headers={"Retry-After": "7"}, json={"message": "slow", "code": "RATE_LIMITED"}
    )
    route = api.post("/task/p1").mock(side_effect=[limited, httpx.Response(200, json=TASK)])
    task = await client.create_task("p1", title="T", status="to-do")
    assert task.id == "t1"
    assert route.call_count == 2
    assert sleep.calls == [7.0]


async def test_rate_limit_gives_up(client, api, sleep):
    limited = httpx.Response(
        429, headers={"Retry-After": "600"}, json={"message": "slow", "code": "RATE_LIMITED"}
    )
    route = api.post("/task/p1").mock(return_value=limited)
    with pytest.raises(KaneoError) as exc:
        await client.create_task("p1", title="T", status="to-do")
    assert exc.value.code == "RATE_LIMITED"
    assert route.call_count == MAX_RATE_LIMIT_WAITS + 1
    assert sleep.calls == [60.0] * MAX_RATE_LIMIT_WAITS


async def test_unexpected_response_shape(client, api):
    api.post("/project").respond(json={"unexpected": True})
    with pytest.raises(KaneoError, match="unexpected response format"):
        await client.create_project("w1", "X", "X")


async def test_non_json_success(client, api):
    api.get("/workspace").respond(200, text="hello")
    with pytest.raises(KaneoError, match="non-JSON"):
        await client.list_workspaces()


# Device flow


@pytest.fixture
async def anonymous(sleep):
    async with KaneoClient(BASE, sleep=sleep) as c:
        yield c


async def test_request_device_code(anonymous, api):
    route = api.post("/auth/device/code").respond(
        json={
            "device_code": "dev",
            "user_code": "ABCD1234",
            "verification_uri": "https://kaneo.test/device",
            "verification_uri_complete": "https://kaneo.test/device?user_code=ABCD1234",
            "expires_in": 1800,
            "interval": 5,
        }
    )
    code = await anonymous.request_device_code("kaneo-templates")
    assert code.user_code == "ABCD1234"
    assert code.interval == 5
    assert code.expires_in == 1800
    assert body(route) == {"client_id": "kaneo-templates"}
    assert "Authorization" not in route.calls.last.request.headers


async def test_request_device_code_client_not_allowed(anonymous, api):
    api.post("/auth/device/code").respond(
        400, json={"error": "invalid_client", "error_description": "Invalid client ID"}
    )
    with pytest.raises(KaneoError, match="DEVICE_AUTH_CLIENT_IDS") as exc:
        await anonymous.request_device_code("kaneo-templates")
    assert exc.value.code == "invalid_client"


async def test_poll_device_token_approved(anonymous, api):
    route = api.post("/auth/device/token").respond(
        json={
            "access_token": "session-token",
            "token_type": "Bearer",
            "expires_in": 604800,
            "scope": "",
        }
    )
    poll = await anonymous.poll_device_token("kaneo-templates", "dev")
    assert poll == DevicePoll("approved", "session-token", 604800)
    assert body(route) == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": "dev",
        "client_id": "kaneo-templates",
    }


@pytest.mark.parametrize(
    ("error_code", "status"),
    [
        ("authorization_pending", "pending"),
        ("slow_down", "slow_down"),
        ("access_denied", "denied"),
        ("expired_token", "expired"),
    ],
)
async def test_poll_device_token_states(anonymous, api, error_code, status):
    api.post("/auth/device/token").respond(
        400, json={"error": error_code, "error_description": "..."}
    )
    assert await anonymous.poll_device_token("kaneo-templates", "dev") == DevicePoll(status)


async def test_poll_device_token_invalid_grant(anonymous, api):
    api.post("/auth/device/token").respond(
        400, json={"error": "invalid_grant", "error_description": "Invalid device code"}
    )
    with pytest.raises(KaneoError) as exc:
        await anonymous.poll_device_token("kaneo-templates", "dev")
    assert exc.value.code == "invalid_grant"


async def test_current_user_from_session(client, api):
    api.get("/auth/get-session").respond(
        json={"session": {"id": "s"}, "user": {"id": "u1", "name": "Ada Lovelace", "email": "a@x"}}
    )
    user = await client.current_user()
    assert (user.id, user.name, user.email, user.initials) == ("u1", "Ada Lovelace", "a@x", "AL")


@pytest.mark.parametrize("reply", [{"json": None}, {"json": {"session": None}}, {"json": []}])
async def test_current_user_none_for_api_keys(client, api, reply):
    api.get("/auth/get-session").respond(**reply)
    assert await client.current_user() is None


async def test_current_user_none_on_401(client, api):
    api.get("/auth/get-session").respond(401, json={"message": "Unauthorized"})
    assert await client.current_user() is None


async def test_current_user_initials_fallbacks():
    from kaneo_templates.kaneo_client import User

    assert User(id="1", name="ada").initials == "A"
    assert User(id="1", name="Jean Paul Sartre").initials == "JP"
    assert User(id="1", email="bob@example.com").initials == "B"
    assert User(id="1").initials == "?"
