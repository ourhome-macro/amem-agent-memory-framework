"""Make an online SQLite backup and a private E2E environment from this workstation."""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def main():
    source = ROOT / "recommend-radio" / "server-data"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = ROOT / ".amem" / ("framework-e2e-" + stamp)
    data = target / "data"
    traces = target / "traces"
    data.mkdir(parents=True, exist_ok=False)
    traces.mkdir()
    inventory = {}
    for name in ("bili_radio.sqlite3", "amem.sqlite3"):
        source_path = (source / name).resolve()
        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(data / name) as backup:
                original.backup(backup)
                assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                inventory[name] = {
                    "schema_version": backup.execute("PRAGMA user_version").fetchone()[0]
                }
                if name == "bili_radio.sqlite3":
                    for table in ("tracks", "likes", "app_users", "bili_accounts"):
                        inventory[name][table] = backup.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
    for path in source.glob("*.auth.key"):
        shutil.copy2(path, data / path.name)
    values = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / "recommend-radio" / ".env")}
    values = {k: v for k, v in values.items() if v is not None}
    values.update(
        {
            "E2E_RUN_DIR": target.as_posix(),
            "E2E_DATA_DIR": data.as_posix(),
            "E2E_ENV_FILE": (target / "private.env").as_posix(),
            "E2E_DATA_VOLUME": "amem-e2e-data-" + stamp,
            "AUTH_MODE": "disabled",
            "ALLOW_INSECURE_LOCAL_AUTH": "1",
            "SESSION_COOKIE_SECURE": "false",
            "APP_SECRET_KEY": secrets.token_urlsafe(48),
            "SSE_INTERNAL_TOKEN": secrets.token_urlsafe(32),
            "RABBITMQ_USER": "radio_e2e",
            "RABBITMQ_PASSWORD": secrets.token_urlsafe(32),
            "RABBITMQ_HOST": "rabbitmq",
            "RABBITMQ_PORT": "5672",
            "RABBITMQ_ENABLED": "true",
            "APP_DATA_DIR": "/app/data",
            "AMEM_DB_PATH": "/app/data/amem.sqlite3",
            "AMEM_TRANSPORT": "grpc",
            "AMEM_GRPC_ADDR": "amem:9090",
            "AMEM_EMBEDDING_PROVIDER": "openai-compatible",
            "AMEM_EMBEDDING_BASE_URL": "http://host.docker.internal:8001/v1",
            "AMEM_EMBEDDING_MODEL": "bge-m3",
            "AMEM_EMBEDDING_DIMENSIONS": "1024",
            "AMEM_EMBEDDING_API_KEY_ENV": "BGE_M3_API_KEY",
            "BGE_M3_API_KEY": "local-embedding",
            "AMEM_EMBEDDING_MIN_SIMILARITY": "0.45",
            "RECOMMEND_EMBEDDING_BASE_URL": "http://host.docker.internal:8001/v1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318",
            "SSE_GATEWAY_INTERNAL_URL": "http://sse-gateway:8080/internal/events",
            "SSE_AUTH_VERIFY_URL": "http://backend:5000/api/session/me",
            "REQUIRE_DB_MIGRATION": "1",
            "OTEL_BSP_SCHEDULE_DELAY": "500",
        }
    )
    env_file = target / "private.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    (target / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"run_dir": str(target), "env_file": str(env_file), "inventory": inventory},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
