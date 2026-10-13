from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TextIO

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from kaneo_templates import __version__, auth
from kaneo_templates.config import ConfigError, Settings, load_settings
from kaneo_templates.loader import load_templates
from kaneo_templates.web.routes import router

STATIC_DIR = Path(__file__).parent / "web" / "static"

logger = logging.getLogger("kaneo_templates")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        try:
            settings = load_settings()
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from None

    _configure_logging(settings.log_level)
    logger.info("Starting kaneo-templates %s (auth mode: %s)", __version__, settings.auth_mode)

    app = FastAPI(
        title="kaneo-templates",
        version=__version__,
        root_path=settings.root_path,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    auth.install(app, settings)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def check(directory: Path, out: TextIO | None = None) -> int:
    """Validate every template of `directory`; exit status 0 only when all are valid."""
    out = out or sys.stdout
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=out)
        return 2
    previous = logging.root.manager.disable
    logging.disable(logging.WARNING)  # the report below already lists the problems
    try:
        result = load_templates(directory)
    finally:
        logging.disable(previous)
    for template_id, template in sorted(result.templates.items()):
        subtasks = sum(len(task.subtasks) for task in template.tasks)
        print(f"ok     {template_id}: {len(template.tasks)} tasks, {subtasks} subtasks", file=out)
    for key, reason in sorted(result.errors.items()):
        print(f"error  {key}: {reason}", file=out)
    if not result.templates and not result.errors:
        print(f"error: no .yml or .yaml template in {directory}", file=out)
        return 1
    return 1 if result.errors else 0


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kaneo-templates",
        description="Create Kaneo projects from YAML templates.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="start the web interface (default)")
    check_parser = commands.add_parser("check", help="validate the templates of a directory")
    check_parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path(os.environ.get("TEMPLATES_DIR") or "/data/templates"),
        help="templates directory (default: TEMPLATES_DIR or /data/templates)",
    )
    args = parser.parse_args(argv)
    if args.command == "check":
        raise SystemExit(check(args.directory))
    serve()


def serve() -> None:
    import uvicorn

    uvicorn.run(
        "kaneo_templates.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - container default
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
    )
