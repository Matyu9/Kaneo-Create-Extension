"""Checks the client against a real Kaneo instance, with a throwaway project.

Deselected by default. Run with:

    KANEO_BASE_URL=... KANEO_TOKEN=... uv run pytest -m integration

Optional: KANEO_WORKSPACE_ID selects the workspace (first one otherwise).
Everything created is deleted at the end, including the workspace label.
"""

import os
import secrets
import string
from datetime import date

import pytest

from kaneo_templates.kaneo_client import KaneoClient

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

BASE_URL = os.environ.get("KANEO_BASE_URL", "")
TOKEN = os.environ.get("KANEO_TOKEN", "")


def _suffix(n: int = 4) -> str:
    return "".join(secrets.choice(string.ascii_uppercase) for _ in range(n))


@pytest.mark.skipif(not (BASE_URL and TOKEN), reason="KANEO_BASE_URL and KANEO_TOKEN not set")
async def test_throwaway_project_round_trip():
    async with KaneoClient(BASE_URL, TOKEN) as kaneo:
        workspaces = await kaneo.validate_token()
        assert workspaces, "the token has no workspace"
        workspace_id = os.environ.get("KANEO_WORKSPACE_ID") or workspaces[0].id

        existing_keys = {p.slug.upper() for p in await kaneo.list_projects(workspace_id)}
        key = "ZT" + _suffix()
        assert key not in existing_keys

        label_name = f"kaneo-templates-it-{_suffix(6).lower()}"
        labels = await kaneo.list_workspace_labels(workspace_id)
        assert label_name not in {label.name for label in labels}

        project = await kaneo.create_project(
            workspace_id, f"kaneo-templates integration test {key}", key
        )
        label_id = None
        try:
            columns = await kaneo.list_columns(project.id)
            assert [c.slug for c in columns] == ["to-do", "in-progress", "in-review", "done"]

            extra = await kaneo.create_column(project.id, "Intégration test")
            assert extra.slug == "intégration-test"
            in_review = next(c for c in columns if c.slug == "in-review")
            await kaneo.delete_column(in_review.id)
            order = [extra.id] + [c.id for c in columns if c.slug != "in-review"]
            await kaneo.reorder_columns(project.id, order)
            assert [c.id for c in await kaneo.list_columns(project.id)] == order

            parent = await kaneo.create_task(
                project.id,
                title="Parent",
                status=extra.slug,
                priority="high",
                due_date=date(2030, 1, 15),
            )
            assert parent.status == extra.slug
            assert parent.due_date is not None
            assert parent.due_date.startswith("2030-01-15")

            child = await kaneo.create_task(project.id, title="Child", status="to-do")
            relation = await kaneo.link_subtask(parent.id, child.id)
            assert relation.source_task_id == parent.id
            relations = await kaneo.list_task_relations(parent.id)
            assert any(
                r.target_task_id == child.id and r.relation_type == "subtask" for r in relations
            )

            definition = await kaneo.create_label(workspace_id, label_name, "#6b7280")
            label_id = definition.id
            assert definition.is_definition
            bound = await kaneo.attach_label(definition.id, parent.id)
            assert bound.task_id == parent.id
            labels = await kaneo.list_workspace_labels(workspace_id)
            assert any(label.id == definition.id and label.is_definition for label in labels)
        finally:
            await kaneo.delete_project(project.id)
            if label_id is not None:
                await kaneo.delete_label(label_id)

        remaining = await kaneo.list_projects(workspace_id)
        assert project.id not in {p.id for p in remaining}
