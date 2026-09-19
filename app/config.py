from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = "change-me"
    db_host: str = "localhost"
    db_port: int = 5432
    db_username: str = "postgres"
    db_password: str = "postgres"
    db_name: str = "app_db"
    # Prefer ENGINE_BASE_URL. ENGINE_URL (legacy .../execute) is used only as fallback.
    engine_base_url: str | None = None
    engine_url: str | None = None
    engine_api_key: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_payload: bool = True
    engine_timeout_seconds: float = 120.0

    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str | None = None
    aws_bucket_name: str | None = None
    s3_payload_prefix: str = "hhs/engine-payloads"
    s3_presign_expires_seconds: int = 300

    def resolved_engine_base_url(self) -> str:
        if self.engine_base_url:
            return self.engine_base_url.rstrip("/")
        legacy = (self.engine_url or "").rstrip("/")
        if legacy.endswith("/execute"):
            return legacy[: -len("/execute")].rstrip("/") or "http://localhost:8000"
        if legacy:
            return legacy
        return "http://localhost:8000"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_username}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def s3_configured(self) -> bool:
        return bool(
            self.aws_access_key_id
            and self.aws_secret_access_key
            and self.aws_region
            and self.aws_bucket_name
        )

    @property
    def engine_full_assignment_url(self) -> str:
        # engine-service mounts jobs under /engine-api (see engine-service/app/main.py)
        return f"{self.resolved_engine_base_url()}/engine-api/api/v1/scheduler/full-assignment"

    @property
    def engine_multicpsat_url(self) -> str:
        return f"{self.resolved_engine_base_url()}/engine-api/api/v1/scheduler/multicpsat"

    @property
    def engine_reschedule_url(self) -> str:
        return f"{self.resolved_engine_base_url()}/engine-api/api/v1/scheduler/reschedule"

    def engine_job_url(self, job_id: str) -> str:
        return f"{self.resolved_engine_base_url()}/engine-api/api/v1/jobs/{job_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
