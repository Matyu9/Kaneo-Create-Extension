"""Load templates from TEMPLATES_DIR.

Invalid files are skipped and reported. Error messages never include template content,
since templates can hold internal information and the messages end up in logs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from kaneo_templates.schema import Template

logger = logging.getLogger(__name__)

TEMPLATE_SUFFIXES = (".yml", ".yaml")
TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_TEMPLATE_BYTES = 256 * 1024


@dataclass
class LoadResult:
    templates: dict[str, Template] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def get(self, template_id: str) -> Template | None:
        """Look up by identifier only; never builds a path from the given value."""
        return self.templates.get(template_id)


class TemplateError(Exception):
    pass


def parse_template(text: str) -> Template:
    try:
        data = yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        where = f" at line {exc.problem_mark.line + 1}" if exc.problem_mark else ""
        raise TemplateError(f"invalid YAML{where}: {exc.problem or 'syntax error'}") from None
    except yaml.YAMLError:
        raise TemplateError("invalid YAML") from None
    if not isinstance(data, dict):
        raise TemplateError("the file must contain a YAML mapping")
    try:
        return Template.model_validate(data)
    except ValidationError as exc:
        raise TemplateError(_format_validation_error(exc)) from None


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err["loc"])
        loc = loc.removeprefix(".")
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def load_templates(directory: Path) -> LoadResult:
    result = LoadResult()
    if not directory.is_dir():
        logger.warning("Templates directory not found: %s", directory)
        return result

    files = sorted(
        p
        for p in directory.iterdir()
        if p.suffix.lower() in TEMPLATE_SUFFIXES and not p.name.startswith(".")
    )
    for path in files:
        template_id = path.stem
        if not TEMPLATE_ID_RE.match(template_id):
            _reject(result, path.name, "file name must use letters, digits, '-' or '_'")
            continue
        if template_id in result.templates or template_id in result.errors:
            _reject(result, path.name, f"another file already defines template {template_id!r}")
            continue
        try:
            if not path.is_file():
                raise TemplateError("not a regular file")
            if path.stat().st_size > MAX_TEMPLATE_BYTES:
                raise TemplateError(f"file larger than {MAX_TEMPLATE_BYTES // 1024} KiB")
            text = path.read_text(encoding="utf-8")
            result.templates[template_id] = parse_template(text)
        except UnicodeDecodeError:
            _reject(result, template_id, "file is not valid UTF-8")
        except OSError as exc:
            _reject(result, template_id, f"cannot read file ({exc.strerror or 'I/O error'})")
        except TemplateError as exc:
            _reject(result, template_id, str(exc))
    return result


def _reject(result: LoadResult, key: str, reason: str) -> None:
    logger.warning("Template %s ignored: %s", key, reason)
    result.errors[key] = reason
