from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from kaneo_templates import __version__, auth
from kaneo_templates.config import ConfigError, Settings, load_settings
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


def run() -> None:
    import uvicorn

    uvicorn.run(
        "kaneo_templates.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - container default
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
    )
