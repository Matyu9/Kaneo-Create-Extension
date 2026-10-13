import itertools
import json
from datetime import date

import httpx
import pytest
import respx

from kaneo_templates.creator import (
    Creator,
    build_plan,
    creation_order,
    plan_board,
    project_key,
    unique_project_key,
)
from kaneo_templates.kaneo_client import KaneoClient
from kaneo_templates.loader import parse_template

BASE = "https://kaneo.test/api"
PUBLIC = "https://kaneo.test"

EVENT = parse_template(
    """\
version: 1
name: Event
end_date:
  required: true
columns: [À faire, To do, Terminé]
tasks:
  - title: Book the room
    due: -30d
    priority: high
    column: To Do
    labels: [logistics]
  - title: Write minutes
    due: +3d
    subtasks:
      - title: Proofread
        due: +5d
      - title: Send
  - title: Debrief
    labels: [logistics, team]
"""
)

DEFAULT_COLUMNS = [
    ("to-do", "To Do", 0, False),
    ("in-progress", "In Progress", 1, False),
    ("in-review", "In Review", 2, False),
    ("done", "Done", 3, True),
]


def column(slug, name, position=0, is_final=False, cid=None):
    return {
        "id": cid or f"col-{slug}",
        "projectId": "p1",
        "name": name,
        "slug": slug,
        "position": position,
        "isFinal": is_final,
    }


class FakeKaneo:
    """Minimal stateful Kaneo answering through respx; records the write sequence."""

    def __init__(self, mock, fail_on=None, existing_keys=(), existing_labels=()):
        self.writes = []
        self.fail_on = fail_on
        self.ids = itertools.count(1)
        self.existing_keys = existing_keys
        self.existing_labels = existing_labels
        self.tasks = {}
        mock.route().mock(side_effect=self.handle)

    def handle(self, request):
        method, path = request.method, request.url.path.removeprefix("/api/")
        body = json.loads(request.content) if request.content else None
        if method == "GET":
            return self.read(path)
        self.writes.append((method, path, body))
        if self.fail_on and self.fail_on(method, path, body):
            return httpx.Response(500, json={"message": "boom", "code": "INTERNAL_SERVER_ERROR"})
        return self.write(method, path, body)

    def read(self, path):
        if path == "project":
            return httpx.Response(
                200,
                json=[
                    {"id": f"x{i}", "name": k, "slug": k} for i, k in enumerate(self.existing_keys)
                ],
            )
        if path == "column/p1":
            return httpx.Response(200, json=[column(*c) for c in DEFAULT_COLUMNS])
        if path == "label/workspace/w1":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": f"lbl-{n}",
                        "name": n,
                        "color": "#000",
                        "taskId": None,
                        "workspaceId": "w1",
                    }
                    for n in self.existing_labels
                ]
                + [
                    {
                        "id": "bound",
                        "name": "logistics",
                        "color": "#000",
                        "taskId": "t0",
                        "workspaceId": "w1",
                    }
                ],
            )
        raise AssertionError(f"unexpected read {path}")

    def write(self, method, path, body):
        n = next(self.ids)
        if path == "project":
            return httpx.Response(
                200,
                json={"id": "p1", "workspaceId": "w1", "name": body["name"], "slug": body["slug"]},
            )
        if path == "column/p1":
            from kaneo_templates.schema import column_slug

            return httpx.Response(
                200, json=column(column_slug(body["name"]), body["name"], cid=f"new-{n}")
            )
        if path.startswith("column/reorder") or method == "DELETE":
            return httpx.Response(200, json={})
        if path.startswith("column/"):
            return httpx.Response(200, json=column("to-do", body["name"], cid=path.split("/")[1]))
        if path == "label":
            return httpx.Response(
                200,
                json={
                    "id": f"lbl-{body['name']}",
                    "name": body["name"],
                    "color": body["color"],
                    "taskId": None,
                },
            )
        if path.startswith("label/"):
            return httpx.Response(
                200,
                json={"id": f"copy-{n}", "name": "x", "color": "#000", "taskId": body["taskId"]},
            )
        if path == "task/p1":
            tid = f"t{n}"
            self.tasks[tid] = body
            return httpx.Response(
                200, json={"id": tid, "title": body["title"], "status": body["status"]}
            )
        if path == "task-relation":
            return httpx.Response(200, json={"id": f"r{n}", **body})
        raise AssertionError(f"unexpected write {method} {path}")


async def run(fake_kwargs=None, template=EVENT, end=date(2030, 6, 14), name="Summer party"):
    with respx.mock(base_url=BASE) as mock:
        fake = FakeKaneo(mock, **(fake_kwargs or {}))
        async with KaneoClient(BASE, "tok") as client:
            plan = build_plan(template, end, today=date(2030, 1, 1), project_name=name)
            report = await Creator(client, PUBLIC).run("w1", template, name, plan)
    return fake, report


pytestmark = pytest.mark.anyio


async def test_full_creation_order_and_report():
    fake, report = await run()
    assert report.ok
    assert report.project_key == "SP"
    assert report.project_url == f"{PUBLIC}/dashboard/workspace/w1/project/p1/board"
    assert (report.tasks_created, report.subtasks_created) == (3, 2)
    assert (report.columns_configured, report.labels_created) == (3, 2)

    sequence = [(m, p) for m, p, _ in fake.writes]
    assert sequence[0] == ("POST", "project")
    assert sequence[1:4] == [
        ("POST", "column/p1"),
        ("PUT", "column/col-to-do"),
        ("POST", "column/p1"),
    ]
    assert sorted(sequence[4:7]) == [
        ("DELETE", "column/col-done"),
        ("DELETE", "column/col-in-progress"),
        ("DELETE", "column/col-in-review"),
    ]
    assert sequence[7] == ("PUT", "column/reorder/p1")
    assert sequence[8:10] == [("POST", "label"), ("POST", "label")]
    task_posts = [b["title"] for m, p, b in fake.writes if p == "task/p1"]
    assert task_posts == ["Book the room", "Write minutes", "Debrief", "Proofread", "Send"]
    assert sum(1 for _, p, _ in fake.writes if p == "task-relation") == 2


async def test_task_fields_due_dates_and_columns():
    fake, _ = await run()
    by_title = {b["title"]: b for b in fake.tasks.values()}
    assert by_title["Book the room"]["dueDate"] == "2030-05-15T12:00:00.000Z"
    assert by_title["Book the room"]["status"] == "to-do"
    assert by_title["Book the room"]["priority"] == "high"
    assert by_title["Write minutes"]["status"] == "à-faire"
    assert "dueDate" not in by_title["Debrief"]
    assert by_title["Proofread"]["dueDate"] == "2030-06-19T12:00:00.000Z"
    assert "dueDate" not in by_title["Send"]


async def test_reorder_and_final_column():
    fake, _ = await run()
    update = next(b for m, p, b in fake.writes if p == "column/col-to-do")
    assert update == {"name": "To do", "isFinal": False}
    created = [b for m, p, b in fake.writes if p == "column/p1"]
    assert created == [{"name": "À faire", "isFinal": False}, {"name": "Terminé", "isFinal": True}]
    reorder = next(b for m, p, b in fake.writes if p == "column/reorder/p1")
    assert [c["id"] for c in reorder["columns"]] == ["new-2", "col-to-do", "new-4"]


async def test_labels_use_definitions_never_task_bound_rows():
    fake, report = await run({"existing_labels": ["logistics"]})
    assert report.labels_created == 1
    assert [b["name"] for m, p, b in fake.writes if p == "label"] == ["team"]
    attached = [p for m, p, b in fake.writes if p.startswith("label/")]
    assert attached == [
        "label/lbl-logistics/task",
        "label/lbl-logistics/task",
        "label/lbl-team/task",
    ]


async def test_subtasks_linked_parent_as_source():
    fake, _ = await run()
    parent = next(tid for tid, b in fake.tasks.items() if b["title"] == "Write minutes")
    links = [b for m, p, b in fake.writes if p == "task-relation"]
    assert all(
        link["sourceTaskId"] == parent and link["relationType"] == "subtask" for link in links
    )


async def test_partial_failure_stops_without_retry_or_rollback():
    def fail(method, path, body):
        return path == "task/p1" and body["title"] == "Debrief"

    fake, report = await run({"fail_on": fail})
    assert not report.ok
    assert report.project_id == "p1"
    assert report.tasks_created == 2
    assert report.failures == ["tasks[2]: boom"]
    assert sum(1 for _, p, b in fake.writes if p == "task/p1" and b["title"] == "Debrief") == 1
    assert not any(m == "DELETE" and p.startswith("project") for m, p, _ in fake.writes)
    assert not any(p == "task/p1" and b["title"] == "Proofread" for _, p, b in fake.writes)


async def test_project_creation_failure():
    fake, report = await run({"fail_on": lambda m, p, b: p == "project"})
    assert report.project_id is None
    assert report.failures == ["create project: boom"]
    assert len(fake.writes) == 1


async def test_template_without_columns_keeps_kaneo_columns():
    template = parse_template(
        "version: 1\nname: X\ntasks:\n"
        "  - title: A\n    column: In Progress\n"
        "  - title: B\n    column: Nope\n"
    )
    fake, report = await run(template=template, end=None)
    assert not any(p.startswith("column") for _, p, _ in fake.writes)
    statuses = [b["status"] for b in fake.tasks.values()]
    assert statuses == ["in-progress", "to-do"]
    assert report.warnings == ["tasks[1]: column_not_found"]


async def test_unique_key_against_existing_projects():
    _, report = await run({"existing_keys": ["sp", "SP2"]})
    assert report.project_key == "SP3"


def test_plan_flags_weekend_and_past():
    plan = build_plan(EVENT, date(2030, 6, 14), today=date(2030, 5, 20))
    book = plan.items[0]
    assert book.due == date(2030, 5, 15)
    assert book.past
    assert not book.weekend
    proofread = plan.items[2]
    assert proofread.due == date(2030, 6, 19)
    assert "past_dates" in plan.warnings
    sat = build_plan(EVENT, date(2030, 6, 12), today=date(2030, 1, 1)).items[1]
    assert sat.due == date(2030, 6, 15)
    assert sat.weekend


def test_plan_without_end_date():
    template = parse_template(
        "version: 1\nname: X\nend_date:\n  required: false\ntasks:\n  - title: A\n    due: -1d\n"
    )
    plan = build_plan(template, None, today=date(2030, 1, 1))
    assert plan.items[0].due is None
    assert plan.warnings == ["no_end_date"]
    assert plan.columns == ["To Do", "In Progress", "In Review", "Done"]
    assert not plan.custom_columns


def test_subtask_inherits_parent_column():
    template = parse_template(
        "version: 1\nname: X\ncolumns: [A, B]\ntasks:\n  - title: T\n    column: B\n"
        "    subtasks:\n      - title: S\n"
    )
    plan = build_plan(template, None, today=date(2030, 1, 1))
    assert plan.items[1].column == "B"


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Summer party", "SP"),
        ("Kaneo", "KAN"),
        ("Réunion du conseil d'administration", "RDC"),
        ("  -- été 2030", "É2"),
        ("!!!", ""),
        ("a", "A"),
    ],
)
def test_project_key(name, key):
    assert project_key(name) == key


def test_unique_project_key_fallback():
    assert unique_project_key("!!!", set()) == "PRJ"
    assert unique_project_key("!!!", {"prj"}) == "PRJ2"


# Preview board


def board_titles(plan):
    return {c.name: [card.item.title for card in c.cards] for c in plan_board(plan)}


def test_board_places_cards_like_the_creator():
    plan = build_plan(EVENT, date(2030, 6, 14), date(2030, 1, 1))
    # "To Do" matches the template column "To do" by name, case-insensitively.
    assert board_titles(plan) == {
        "À faire": ["Write minutes", "Debrief", "Proofread", "Send"],
        "To do": ["Book the room"],
        "Terminé": [],
    }


def test_board_numbers_in_creation_order():
    plan = build_plan(EVENT, date(2030, 6, 14), date(2030, 1, 1))
    numbers = {card.item.title: card.number for c in plan_board(plan) for card in c.cards}
    assert numbers == {
        "Book the room": 1,
        "Write minutes": 2,
        "Debrief": 3,
        "Proofread": 4,
        "Send": 5,
    }


def test_board_subtask_links():
    plan = build_plan(EVENT, None, date(2030, 1, 1))
    cards = {card.item.title: card for c in plan_board(plan) for card in c.cards}
    assert cards["Write minutes"].subtask_count == 2
    assert cards["Proofread"].parent_title == "Write minutes"
    assert cards["Book the room"].parent_title is None
    assert cards["Book the room"].subtask_count == 0


def test_creation_order_lists_subtasks_last():
    plan = build_plan(EVENT, None, date(2030, 1, 1))
    titles = [plan.items[i].title for i in creation_order(plan)]
    assert titles == ["Book the room", "Write minutes", "Debrief", "Proofread", "Send"]


def test_board_with_kaneo_default_columns():
    template = parse_template(
        """\
version: 1
name: Plain
tasks:
  - title: By slug
    column: in-review
  - title: By name
    column: done
  - title: Unknown
    column: Somewhere
  - title: Nothing
"""
    )
    board = plan_board(build_plan(template, None, date(2030, 1, 1)))
    assert [c.name for c in board] == ["To Do", "In Progress", "In Review", "Done"]
    titles = {c.name: [card.item.title for card in c.cards] for c in board}
    assert titles == {
        "To Do": ["Unknown", "Nothing"],
        "In Progress": [],
        "In Review": ["By slug"],
        "Done": ["By name"],
    }
    missing = [card.item.title for c in board for card in c.cards if card.column_missing]
    assert missing == ["Unknown"]


def test_board_empty_template():
    template = parse_template("version: 1\nname: Empty\ncolumns: [A, B]\n")
    board = plan_board(build_plan(template, None, date(2030, 1, 1)))
    assert [(c.name, c.cards) for c in board] == [("A", []), ("B", [])]


COLORED = parse_template(
    """\
version: 1
name: Colored
labels:
  logistics: "#f59e0b"
tasks:
  - title: A
    labels: [logistics, team]
"""
)


async def test_new_labels_use_template_colors():
    fake, report = await run(template=COLORED, end=None)
    created = {b["name"]: b["color"] for m, p, b in fake.writes if p == "label"}
    assert created == {"logistics": "#f59e0b", "team": "#6b7280"}
    assert report.labels_created == 2


async def test_existing_labels_keep_their_color():
    fake, _ = await run({"existing_labels": ["logistics"]}, template=COLORED, end=None)
    assert [b["name"] for m, p, b in fake.writes if p == "label"] == ["team"]
    assert not [p for m, p, b in fake.writes if m == "PUT" and p == "label/lbl-logistics"]


VARS = parse_template(
    """\
version: 1
name: Vars
end_date:
  required: false
tasks:
  - title: "Affiche {{project_name}}"
    description: "Date : {{end_date}}"
    subtasks:
      - title: "{{ project_name }} / impression"
"""
)


def test_plan_renders_variables():
    plan = build_plan(VARS, date(2027, 3, 24), date(2027, 1, 1), project_name="Gala")
    assert [i.title for i in plan.items] == ["Affiche Gala", "Gala / impression"]
    assert plan.items[0].description == "Date : 24/03/2027"


def test_plan_variables_date_format_and_missing_date():
    plan = build_plan(VARS, date(2027, 3, 24), date(2027, 1, 1), date_format="%Y-%m-%d")
    assert plan.items[0].description == "Date : 2027-03-24"
    assert plan.items[0].title == "Affiche "
    plan = build_plan(VARS, None, date(2027, 1, 1), project_name="Gala")
    assert plan.items[0].description == "Date : "


async def test_creation_sends_rendered_titles():
    fake, _ = await run(template=VARS, end=date(2027, 3, 24), name="Gala")
    tasks = [b for m, p, b in fake.writes if p == "task/p1"]
    assert [b["title"] for b in tasks] == ["Affiche Gala", "Gala / impression"]
    assert tasks[0]["description"] == "Date : 24/03/2027"


def test_plan_flags_holidays():
    from kaneo_templates.holidays import HolidayCalendar

    template = parse_template(
        "version: 1\nname: H\nend_date:\ntasks:\n  - title: A\n    due: -7d\n"
        "  - title: B\n    due: -1b\n"
    )
    plan = build_plan(template, date(2026, 5, 15), date(2026, 1, 1))
    assert [i.holiday for i in plan.items] == [None, None]
    plan = build_plan(template, date(2026, 5, 15), date(2026, 1, 1), holidays=HolidayCalendar("fr"))
    assert [(i.due, i.holiday) for i in plan.items] == [
        (date(2026, 5, 8), "victory_day"),
        (date(2026, 5, 13), None),  # Thursday 14 May is Ascension
    ]
