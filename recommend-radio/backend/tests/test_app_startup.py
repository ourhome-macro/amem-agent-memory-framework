import os
import subprocess
import sys
from pathlib import Path


def test_http_app_starts_with_isolated_database(tmp_path):
    environment = dict(
        os.environ,
        APP_DATA_DIR=str(tmp_path),
        AUTH_MODE="disabled",
        AMEM_ENABLED="false",
        AMEM_TRANSPORT="embedded",
        RABBITMQ_ENABLED="false",
        RECOMMEND_LLM_ENABLED="false",
        REQUIRE_DB_MIGRATION="0",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app import app; r=app.test_client().get('/health/ready'); "
            "assert r.status_code == 200, r.data; print('HTTP readiness OK')",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "HTTP readiness OK" in result.stdout
