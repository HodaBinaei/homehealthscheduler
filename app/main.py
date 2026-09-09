from __future__ import annotations

import logging

from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes.schedule import router as schedule_router
from app.config import get_settings
from app.db.session import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

app = FastAPI(
    title="Home Health Scheduler Bridge",
    description="Builds Panel-compatible engine execute payloads from PostgreSQL and submits them to the engine.",
    version="1.0.0",
)
app.include_router(schedule_router)


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
