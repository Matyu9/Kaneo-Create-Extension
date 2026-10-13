"""Turn a template into a Kaneo project.

`build_plan` is pure and feeds both the preview and the creation. `create_project`
performs the writes in the order of SPEC §7 and stops at the first failed write:
nothing is rolled back and the report says where it stopped.
"""

from __future__ import annotations

import contextlib
import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from kaneo_templates.kaneo_client import Column, KaneoAuthError, KaneoClient, KaneoError
from kaneo_templates.scheduling import compute_due, is_weekend
from kaneo_templates.schema import SubtaskSpec, Template, column_slug

logger = logging.getLogger(__name__)

# Columns Kaneo seeds on every new project (apps/api/src/project/controllers/insert-project.ts).
KANEO_DEFAULT_COLUMNS = ("To Do", "In Progress", "In Review", "Done")
DEFAULT_LABEL_COLOR = "#6b7280"
FALLBACK_PROJECT_KEY = "PRJ"


@dataclass
class PlannedItem:
    path: str
    title: str
    description: str | None
    priority: str | None
    column: str | None
    labels: list[str]
    due: date | None
    weekend: bool = False
    past: bool = False
    parent_index: int | None = None

    @property
    def is_subtask(self) -> bool:
        return self.parent_index is not None


@dataclass
class Plan:
    columns: list[str]
    custom_columns: bool
    items: list[PlannedItem]
    warnings: list[str]

    @property
    def tasks(self) -> list[PlannedItem]:
        return [i for i in self.items if not i.is_subtask]

    @property
    def subtasks(self) -> list[PlannedItem]:
        return [i for i in self.items if i.is_subtask]


def build_plan(template: Template, end_date: date | None, today: date) -> Plan:
    columns = list(template.columns) if template.columns else list(KANEO_DEFAULT_COLUMNS)
    items: list[PlannedItem] = []
    warnings: list[str] = []
    for i, task in enumerate(template.tasks):
        parent = _planned(f"tasks[{i}]", task, None, end_date, today)
        items.append(parent)
        parent_index = len(items) - 1
        for j, sub in enumerate(task.subtasks):
            child = _planned(f"tasks[{i}].subtasks[{j}]", sub, parent_index, end_date, today)
            if child.column is None:
                child.column = parent.column
            items.append(child)
    if end_date is None and any(spec.due is not None for _, spec in template.iter_items()):
        warnings.append("no_end_date")
    if any(item.past for item in items):
        warnings.append("past_dates")
    return Plan(columns, template.columns is not None, items, warnings)


def _planned(
    path: str, spec: SubtaskSpec, parent: int | None, end: date | None, today: date
) -> PlannedItem:
    due = compute_due(end, spec.due)
    return PlannedItem(
        path=path,
        title=spec.title,
        description=spec.description,
        priority=spec.priority,
        column=spec.column,
        labels=list(spec.labels),
        due=due,
        weekend=due is not None and is_weekend(due),
        past=due is not None and due < today,
        parent_index=parent,
    )


@dataclass
class BoardCard:
    item: PlannedItem
    number: int
    parent_title: str | None
    subtask_count: int
    column_missing: bool


@dataclass
class BoardColumn:
    name: str
    cards: list[BoardCard]


def creation_order(plan: Plan) -> list[int]:
    """Indexes of `plan.items` in creation order: top-level tasks first, then subtasks."""
    tasks = [i for i, it in enumerate(plan.items) if not it.is_subtask]
    return tasks + [i for i, it in enumerate(plan.items) if it.is_subtask]


def plan_board(plan: Plan) -> list[BoardColumn]:
    """Lay the plan out as the Kaneo board will show it, for the preview.

    Cards are placed with the same rule as `Creator._column_for` and numbered in creation
    order, which is how Kaneo numbers the tasks of a new project.
    """
    board = [BoardColumn(name, []) for name in plan.columns]
    for number, index in enumerate(creation_order(plan), start=1):
        item = plan.items[index]
        target, missing = 0, False
        if item.column:
            wanted = column_slug(item.column)
            found = next(
                (
                    i
                    for i, name in enumerate(plan.columns)
                    if column_slug(name) == wanted or name.casefold() == item.column.casefold()
                ),
                None,
            )
            target, missing = (found, False) if found is not None else (0, True)
        parent = plan.items[item.parent_index] if item.parent_index is not None else None
        board[target].cards.append(
            BoardCard(
                item=item,
                number=number,
                parent_title=parent.title if parent else None,
                subtask_count=sum(1 for it in plan.items if it.parent_index == index),
                column_missing=missing,
            )
        )
    return board


def project_key(name: str) -> str:
    """Port of Kaneo's web UI `generateProjectSlug` (apps/web/src/lib/generate-project-id.ts)."""
    text = unicodedata.normalize("NFKC", name).upper()
    kept = "".join(c for c in text if c.isspace() or unicodedata.category(c)[0] in "LMN")
    words = [w for w in kept.split() if any(unicodedata.category(c)[0] in "LN" for c in w)]
    if not words:
        return ""
    if len(words) == 1:
        word = words[0]
        start = next(i for i, c in enumerate(word) if unicodedata.category(c)[0] in "LN")
        return word[start : start + 3]
    return "".join(next(c for c in w if unicodedata.category(c)[0] in "LN") for w in words[:3])


def unique_project_key(name: str, taken: set[str]) -> str:
    base = project_key(name) or FALLBACK_PROJECT_KEY
    taken_norm = {unicodedata.normalize("NFKC", k).lower() for k in taken}
    candidate, n = base, 2
    while candidate.lower() in taken_norm:
        candidate = f"{base}{n}"
        n += 1
    return candidate


@dataclass
class Report:
    project_id: str | None = None
    project_key: str | None = None
    project_url: str | None = None
    tasks_created: int = 0
    subtasks_created: int = 0
    columns_configured: int = 0
    labels_created: int = 0
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    auth_failed: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures


class _Stop(Exception):
    pass


class Creator:
    def __init__(self, client: KaneoClient, public_url: str) -> None:
        self.client = client
        self.public_url = public_url.rstrip("/")
        self.report = Report()

    async def run(self, workspace_id: str, template: Template, name: str, plan: Plan) -> Report:
        with contextlib.suppress(_Stop):
            await self._run(workspace_id, template, name, plan)
        return self.report

    async def _step(self, what: str, coro):
        """Await one Kaneo call; on failure record it and stop the whole creation."""
        try:
            return await coro
        except KaneoAuthError as exc:
            self.report.auth_failed = True
            self.report.failures.append(f"{what}: {exc.message}")
            raise _Stop from None
        except KaneoError as exc:
            logger.warning("Creation stopped at %s (%s %s)", what, exc.status, exc.code)
            self.report.failures.append(f"{what}: {exc.message}")
            raise _Stop from None

    async def _run(self, workspace_id: str, template: Template, name: str, plan: Plan) -> None:
        r = self.report
        projects = await self._step("list projects", self.client.list_projects(workspace_id))
        key = unique_project_key(name, {p.slug for p in projects})
        project = await self._step(
            "create project", self.client.create_project(workspace_id, name, key)
        )
        r.project_id, r.project_key = project.id, project.slug
        r.project_url = (
            f"{self.public_url}/dashboard/workspace/{workspace_id}/project/{project.id}/board"
        )

        columns = await self._setup_columns(project.id, template)
        labels = await self._resolve_labels(workspace_id, template)

        task_ids: dict[int, str] = {}
        for index in creation_order(plan):
            item = plan.items[index]
            column = self._column_for(item, columns)
            task = await self._step(
                item.path,
                self.client.create_task(
                    project.id,
                    title=item.title,
                    status=column.slug,
                    description=item.description,
                    priority=item.priority,
                    due_date=item.due,
                ),
            )
            task_ids[index] = task.id
            for label in item.labels:
                await self._step(
                    f"{item.path} label", self.client.attach_label(labels[label], task.id)
                )
            if item.parent_index is None:
                r.tasks_created += 1
            else:
                await self._step(
                    f"{item.path} link",
                    self.client.link_subtask(task_ids[item.parent_index], task.id),
                )
                r.subtasks_created += 1

    async def _setup_columns(self, project_id: str, template: Template) -> list[Column]:
        existing = await self._step("list columns", self.client.list_columns(project_id))
        if not template.columns:
            if not existing:
                self.report.failures.append("list columns: the project has no column")
                raise _Stop
            return existing
        by_slug = {c.slug: c for c in existing}
        final: list[Column] = []
        last = len(template.columns) - 1
        for i, name in enumerate(template.columns):
            slug = column_slug(name)
            is_final = i == last
            column = by_slug.pop(slug, None)
            if column is None:
                column = await self._step(
                    f"create column {i + 1}",
                    self.client.create_column(project_id, name, is_final=is_final),
                )
            elif column.name != name or column.is_final != is_final:
                column = await self._step(
                    f"update column {i + 1}",
                    self.client.update_column(column.id, name=name, is_final=is_final),
                )
            final.append(column)
        for unused in by_slug.values():
            await self._step(f"delete column {unused.slug}", self.client.delete_column(unused.id))
        await self._step(
            "reorder columns", self.client.reorder_columns(project_id, [c.id for c in final])
        )
        self.report.columns_configured = len(final)
        return final

    async def _resolve_labels(self, workspace_id: str, template: Template) -> dict[str, str]:
        wanted = template.all_labels
        if not wanted:
            return {}
        existing = await self._step("list labels", self.client.list_workspace_labels(workspace_id))
        ids = {lbl.name: lbl.id for lbl in existing if lbl.is_definition}
        for name in wanted:
            if name not in ids:
                label = await self._step(
                    "create label",
                    self.client.create_label(
                        workspace_id, name, template.label_color(name) or DEFAULT_LABEL_COLOR
                    ),
                )
                ids[name] = label.id
                self.report.labels_created += 1
        return ids

    def _column_for(self, item: PlannedItem, columns: list[Column]) -> Column:
        if item.column:
            wanted = column_slug(item.column)
            for column in columns:
                if column.slug == wanted or column.name.casefold() == item.column.casefold():
                    return column
            self.report.warnings.append(f"{item.path}: column_not_found")
        return columns[0]
