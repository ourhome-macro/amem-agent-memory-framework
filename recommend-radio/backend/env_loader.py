from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def load_recommend_radio_env() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    backend_dir = Path(__file__).resolve().parent
    project_dir = backend_dir.parent
    workspace_dir = project_dir.parent

    for env_path in (project_dir / ".env", workspace_dir / ".env"):
        if not env_path.exists():
            continue
        if _load_with_dotenv(env_path):
            continue
        _load_simple_env(env_path)


def _load_with_dotenv(env_path: Path) -> bool:
    try:
        from dotenv import load_dotenv
    except Exception:
        return False
    load_dotenv(env_path, override=False)
    return True


def _load_simple_env(env_path: Path) -> None:
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("'\"")
        os.environ[key] = value
