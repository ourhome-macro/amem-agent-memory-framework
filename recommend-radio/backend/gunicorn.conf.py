from __future__ import annotations

import os
from pathlib import Path


bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")
if (
    os.environ.get("AUTH_MODE", "disabled").strip().lower() == "disabled"
    and not bind.startswith(("127.0.0.1:", "localhost:", "[::1]:"))
    and os.environ.get("ALLOW_INSECURE_LOCAL_AUTH", "").strip().lower()
    not in {"1", "true", "yes", "on"}
):
    raise RuntimeError(
        "AUTH_MODE=disabled may only bind to loopback; set "
        "ALLOW_INSECURE_LOCAL_AUTH=1 to acknowledge the risk"
    )
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("WEB_THREADS", "16"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEP_ALIVE", "5"))
accesslog = "-"
access_log_format = (
    '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" '
    '%(s)s %(b)s "%(f)s" "%(a)s" %(L)s'
)
errorlog = "-"
capture_output = True
preload_app = False


def on_starting(_server) -> None:
    """Clear stale metric shards once, before Gunicorn forks workers."""
    directory = _multiprocess_directory()
    if directory is None:
        return

    directory.mkdir(parents=True, exist_ok=True)
    for shard in directory.iterdir():
        if shard.is_file() or shard.is_symlink():
            shard.unlink()


def child_exit(_server, worker) -> None:
    if _multiprocess_directory() is None:
        return

    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)


def _multiprocess_directory() -> Path | None:
    raw = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()
    if not raw:
        return None

    directory = Path(raw).resolve()
    if directory == Path(directory.anchor):
        raise RuntimeError("PROMETHEUS_MULTIPROC_DIR must not be a filesystem root")
    return directory
