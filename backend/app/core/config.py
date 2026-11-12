import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", "postgresql+psycopg://queueflow:queueflow_local@localhost:5432/queueflow"))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    storage_root: Path = field(default_factory=lambda: Path(os.getenv("STORAGE_ROOT", "data")))
    owner_id: str = "local"
    stream: str = "queueflow:jobs"
    dead_letter_stream: str = "queueflow:dead-letter"
    group: str = "queueflow-workers"
    max_file_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_FILE_BYTES", 20 * 1024 * 1024)))
    max_rows: int = field(default_factory=lambda: int(os.getenv("MAX_ROWS", "100000")))
    max_cell_chars: int = field(default_factory=lambda: int(os.getenv("MAX_CELL_CHARS", "16384")))
    max_row_chars: int = 262144
    max_columns: int = 100
    max_active_jobs: int = field(default_factory=lambda: int(os.getenv("MAX_ACTIVE_JOBS", "100")))
    uploads_per_minute: int = field(default_factory=lambda: int(os.getenv("UPLOADS_PER_MINUTE", "30")))
    max_storage_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_STORAGE_BYTES", 1024**3)))
    lease_seconds: float = field(default_factory=lambda: float(os.getenv("LEASE_SECONDS", "30")))
    heartbeat_seconds: float = field(default_factory=lambda: float(os.getenv("HEARTBEAT_SECONDS", "5")))
    reclaim_idle_ms: int = field(default_factory=lambda: int(os.getenv("RECLAIM_IDLE_MS", "30000")))
    retry_base_seconds: float = field(default_factory=lambda: float(os.getenv("RETRY_BASE_SECONDS", "5")))
    reconcile_seconds: float = field(default_factory=lambda: float(os.getenv("RECONCILE_SECONDS", "60")))
    max_attempts: int = field(default_factory=lambda: int(os.getenv("MAX_ATTEMPTS", "3")))
    retention_hours: int = field(default_factory=lambda: int(os.getenv("RETENTION_HOURS", "168")))
    cors_origins: list[str] = field(default_factory=lambda: os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","))
    drain_file: str = ".drain"

    def __post_init__(self):
        if self.heartbeat_seconds * 2 >= self.lease_seconds:
            raise ValueError("LEASE_SECONDS must exceed twice HEARTBEAT_SECONDS")
        if min(self.max_file_bytes, self.max_rows, self.max_attempts, self.max_storage_bytes) <= 0:
            raise ValueError("Limits must be positive")
        self.storage_root = self.storage_root.resolve()

    @property
    def draining(self):
        return (self.storage_root / self.drain_file).exists()
