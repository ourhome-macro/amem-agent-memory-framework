from __future__ import annotations

import logging
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter

LOGGER = logging.getLogger("recommend-radio.sse-events")


class SSEEventPublisher:
    """Publish durable task events to the Go SSE gateway."""

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = str(url or os.getenv("SSE_GATEWAY_INTERNAL_URL") or "").rstrip("/")
        self.token = str(token or os.getenv("SSE_INTERNAL_TOKEN") or "local-sse-token")
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0, pool_block=True)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def publish(
        self,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        status: str = "running",
    ) -> int | None:
        if not self.enabled:
            return None
        try:
            response = self.session.post(
                self.url,
                json={
                    "taskId": task_id,
                    "sessionId": session_id,
                    "userId": user_id,
                    "type": event_type,
                    "status": status,
                    "payload": payload or {},
                },
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=(2, 5),
            )
            response.raise_for_status()
            value = response.json()
            if isinstance(value, dict) and value.get("eventId"):
                return int(value["eventId"])
            return None
        except Exception as exc:
            LOGGER.warning("SSE event publish failed: %s", exc)
            return None

    def close(self) -> None:
        self.session.close()
