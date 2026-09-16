"""Opt-in test against an isolated real RabbitMQ; no production database access."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    os.getenv("RABBITMQ_INTEGRATION") != "1" or os.name == "nt",
    reason="isolated broker and Linux Celery worker required",
)
def test_celery_outbox_to_real_grpc_amem_and_duplicate_delivery(tmp_path, monkeypatch):
    import database
    import grpc
    import task_app
    from amem_bridge import AmemBridge
    from amem_grpc_server import AmemGrpcService, amem_pb2_grpc
    from durable_jobs import enqueue_behavior, publish_pending

    monkeypatch.setenv("RABBITMQ_ENABLED", "true")
    monkeypatch.setenv("AMEM_EMBEDDING_MODEL", "")
    path = tmp_path / "bili_radio.sqlite3"
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DEFAULT_DB_PATH", path)
    monkeypatch.setattr(task_app, "DEFAULT_DB_PATH", path)
    database.init_db(path)
    bridge = AmemBridge(str(tmp_path / "amem.sqlite3"))
    grpc_server = grpc.server(ThreadPoolExecutor(max_workers=2))
    service = AmemGrpcService(bridge=bridge)
    amem_pb2_grpc.add_AmemServiceServicer_to_server(service, grpc_server)
    port = grpc_server.add_insecure_port("127.0.0.1:0")
    monkeypatch.setenv("AMEM_GRPC_ADDR", f"127.0.0.1:{port}")
    grpc_server.start()
    worker = None
    log_path = tmp_path / "worker.log"
    try:
        with database.get_connection(path) as conn:
            job_id = enqueue_behavior(
                conn,
                user_id="integration-user",
                event="liked",
                scene="test",
                event_id=uuid4().hex,
            )
        with log_path.open("w") as worker_log:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "task_app",
                    "worker",
                    "--pool=prefork",
                    "--concurrency=1",
                    "--hostname=integration@%h",
                    "--without-mingle",
                    "--without-gossip",
                    "-Q",
                    "radio.events.v2",
                    "--loglevel=INFO",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=os.environ.copy(),
                stdout=worker_log,
                stderr=subprocess.STDOUT,
            )
            publish_pending(path, task_app.publish_job)
            task_app.publish_job(job_id, "behavior")  # Deliberate at-least-once delivery.
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                with database.get_connection(path) as conn:
                    row = conn.execute(
                        "SELECT * FROM durable_jobs WHERE job_id=?", (job_id,)
                    ).fetchone()
                if row["status"] == "completed":
                    break
                time.sleep(0.1)
            assert row["status"] == "completed", (dict(row), log_path.read_text())
            assert row["attempts"] == 1
            # Wait until the duplicate has left the broker as well.
            with task_app.app.connection_for_read() as connection:
                channel = connection.channel()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    _, remaining, _ = channel.queue_declare(queue="radio.events.v2", passive=True)
                    if remaining == 0:
                        break
                    time.sleep(0.1)
                assert remaining == 0, log_path.read_text()
            assert len(bridge.handle.runtime.event_store.list_events()) == 1
    finally:
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(5)
        service._embedding_stop.set()
        grpc_server.stop(3).wait()
