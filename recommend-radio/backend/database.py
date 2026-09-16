from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from env_loader import load_recommend_radio_env
from monitoring import record_database_operation

load_recommend_radio_env()

BASE_DIR = Path(__file__).resolve().parent


def resolve_data_dir(
    *,
    configured_data_dir: Optional[str] = None,
) -> Path:
    explicit_data_dir = (
        configured_data_dir if configured_data_dir is not None else os.getenv("APP_DATA_DIR", "")
    ).strip()
    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser()
    return BASE_DIR / "data"


DATA_DIR = resolve_data_dir()
DEFAULT_DB_PATH = DATA_DIR / "bili_radio.sqlite3"
SQLITE_BUSY_TIMEOUT_MS = 30_000
LEGACY_OWNER_USER_ID = "legacy-owner"

_init_lock = threading.Lock()
_initialized_paths: set[Path] = set()


class ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        self._monitoring_started_at = time.perf_counter()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        outcome = "success"
        if exc_value is not None:
            message = str(exc_value).lower()
            outcome = "busy" if "locked" in message or "busy" in message else "error"
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        except sqlite3.Error as error:
            message = str(error).lower()
            outcome = "busy" if "locked" in message or "busy" in message else "error"
            raise
        finally:
            self.close()
            started_at = getattr(self, "_monitoring_started_at", None)
            if started_at is not None:
                record_database_operation(
                    "transaction",
                    outcome,
                    time.perf_counter() - started_at,
                )


def get_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
        factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    from schema_migrations import ensure_database

    path = (Path(db_path) if db_path else DEFAULT_DB_PATH).resolve()
    with _init_lock:
        if path in _initialized_paths and path.exists():
            return
        ensure_database(path, migrate=os.getenv("REQUIRE_DB_MIGRATION") != "1")
        _initialized_paths.add(path)
