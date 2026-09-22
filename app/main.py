from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes.jobs import router as jobs_router
from app.api.routes.multi_schedule import router as multi_schedule_router
from app.api.routes.optimize import (
    legacy_router as optimize_legacy_router,
    reschedule_alias_router,
    router as optimize_router,
)
from app.api.routes.schedule import router as schedule_router
from app.config import get_settings
from app.db.session import engine
from app.services.runs_store import ensure_runs_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        ensure_runs_table()
        logging.getLogger("hhs").info("hhs_engine_runs table ready")
    except Exception:
        logging.getLogger("hhs").exception(
            "Could not ensure hhs_engine_runs table at startup; "
            "will retry on first job submit"
        )
    settings = get_settings()
    engine_base = settings.resolved_engine_base_url()
    bridge_self = f"http://127.0.0.1:{settings.app_port}"
    bridge_self_alt = f"http://localhost:{settings.app_port}"
    if engine_base.rstrip("/") in {bridge_self, bridge_self_alt}:
        logging.getLogger("hhs").warning(
            "ENGINE_BASE_URL=%s points at this bridge (APP_PORT=%s). "
            "Schedule/Roster/Optimize submits will 404 on /engine-api/... — "
            "set ENGINE_BASE_URL to the real engine-service host "
            "(e.g. http://34.244.104.57/api-engine).",
            engine_base,
            settings.app_port,
        )
    yield


app = FastAPI(
    title="Home Health Scheduler Bridge",
    description=(
        "Builds engine-service scheduler payloads from PostgreSQL and submits "
        "full-assignment, multicpsat, and reschedule jobs."
    ),
    version="1.1.0",
    lifespan=lifespan,
)
app.include_router(schedule_router)
app.include_router(multi_schedule_router)
app.include_router(optimize_router)
app.include_router(reschedule_alias_router)
app.include_router(optimize_legacy_router)
app.include_router(jobs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict[str, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "up"}
    except Exception as exc:
        return {"status": "degraded", "database": f"down: {exc}"}


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
