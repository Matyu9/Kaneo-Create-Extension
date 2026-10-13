"""Template model (YAML format version 1)."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from kaneo_templates.scheduling import Offset, parse_due

SUPPORTED_VERSIONS = (1,)
PRIORITIES = ("no-priority", "low", "medium", "high", "urgent")
RESERVED_COLUMN_SLUGS = ("planned", "archived")

Priority = Literal["no-priority", "low", "medium", "high", "urgent"]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def column_slug(name: str) -> str:
    """Same rule as Kaneo's `toSlug` (apps/api/src/column/controllers/create-column.ts):
    NFKC, lowercase, runs of characters other than letters, marks and digits become `-`."""
    text = unicodedata.normalize("NFKC", name).lower().strip()
    out: list[str] = []
    in_separator = False
    for char in text:
        if unicodedata.category(char)[0] in ("L", "M", "N"):
            out.append(char)
            in_separator = False
        elif not in_separator:
            out.append("-")
            in_separator = True
    slug = "".join(out).strip("-")
    if not any(unicodedata.category(c)[0] in ("L", "N") for c in slug):
        return ""
    return slug


def _parse_offset(value: object) -> Offset | None:
    if value is None or isinstance(value, Offset):
        return value
    return parse_due(value)  # type: ignore[arg-type]


DueOffset = Annotated[Offset | None, BeforeValidator(_parse_offset)]

_HEX_COLOR = re.compile(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})")


def _parse_color(value: object) -> object:
    if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value.strip().lower()):
        raise ValueError("label color must be a hex color such as '#f59e0b'")
    return value.strip().lower()


LabelColor = Annotated[str, BeforeValidator(_parse_color)]

# Placeholders allowed in task titles and descriptions. Substitution is plain text
# replacement, never template evaluation.
VARIABLES = ("project_name", "end_date")
_VARIABLE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def render_text(text: str, values: dict[str, str]) -> str:
    """Replace `{{ name }}` placeholders; names are checked when the template loads."""
    return _VARIABLE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def _variables(text: str | None) -> list[str]:
    return _VARIABLE.findall(text) if text else []


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SubtaskSpec(_Strict):
    title: NonEmptyStr
    description: str | None = None
    priority: Priority | None = None
    column: NonEmptyStr | None = None
    labels: list[NonEmptyStr] = Field(default_factory=list)
    due: DueOffset = None


class TaskSpec(SubtaskSpec):
    subtasks: list[SubtaskSpec] = Field(default_factory=list)


class EndDateSpec(_Strict):
    required: bool = True
    label: NonEmptyStr | None = None


class Template(_Strict):
    version: int
    name: NonEmptyStr
    description: str | None = None
    end_date: EndDateSpec | None = None
    columns: list[NonEmptyStr] | None = None
    labels: dict[NonEmptyStr, LabelColor] = Field(default_factory=dict)
    tasks: list[TaskSpec] = Field(default_factory=list)

    @field_validator("version", mode="before")
    @classmethod
    def _known_version(cls, value: object) -> object:
        if isinstance(value, bool) or value not in SUPPORTED_VERSIONS:
            supported = ", ".join(str(v) for v in SUPPORTED_VERSIONS)
            raise ValueError(f"unsupported template version (supported: {supported})")
        return value

    @field_validator("end_date", mode="before")
    @classmethod
    def _end_date_shorthand(cls, value: object) -> object:
        # `end_date: {}` or a bare `end_date:` key both mean "date field with defaults".
        return {} if value is None else value

    @field_validator("columns")
    @classmethod
    def _valid_columns(cls, columns: list[str] | None) -> list[str] | None:
        if columns is None:
            return None
        if not columns:
            raise ValueError("columns must not be empty; omit the key to keep Kaneo's columns")
        seen: dict[str, int] = {}
        for i, name in enumerate(columns):
            slug = column_slug(name)
            if not slug:
                raise ValueError(f"columns[{i}] must contain at least one letter or digit")
            if slug in RESERVED_COLUMN_SLUGS:
                raise ValueError(f"columns[{i}] uses a name reserved by Kaneo")
            if slug in seen:
                raise ValueError(
                    f"columns[{seen[slug]}] and columns[{i}] map to the same Kaneo column"
                )
            seen[slug] = i
        return columns

    @model_validator(mode="after")
    def _cross_checks(self) -> Template:
        known_slugs = {column_slug(c) for c in self.columns} if self.columns else None
        for path, item in self.iter_items():
            for name in _variables(item.title) + _variables(item.description):
                if name not in VARIABLES:
                    raise ValueError(
                        f"{path}: unknown variable (available: {', '.join(VARIABLES)})"
                    )
                if name == "end_date" and self.end_date is None:
                    raise ValueError(f"{path}: {{{{end_date}}}} requires an 'end_date' section")
            if item.due is not None and self.end_date is None:
                raise ValueError(f"{path}: 'due' requires an 'end_date' section in the template")
            if (
                known_slugs is not None
                and item.column is not None
                and column_slug(item.column) not in known_slugs
            ):
                raise ValueError(f"{path}: column is not listed in 'columns'")
        return self

    def iter_items(self):
        """Yield (path, task or subtask) in creation order."""
        for i, task in enumerate(self.tasks):
            yield f"tasks[{i}]", task
            for j, sub in enumerate(task.subtasks):
                yield f"tasks[{i}].subtasks[{j}]", sub

    @property
    def end_date_required(self) -> bool:
        return self.end_date is not None and self.end_date.required

    def label_color(self, name: str) -> str | None:
        """Color set for a label in the `labels` section, if any."""
        return self.labels.get(name)

    @property
    def all_labels(self) -> list[str]:
        """Distinct label names in order of first use."""
        labels: dict[str, None] = {}
        for _, item in self.iter_items():
            for label in item.labels:
                labels.setdefault(label, None)
        return list(labels)
