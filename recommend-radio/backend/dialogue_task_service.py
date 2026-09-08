from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from sse_event_client import SSEEventPublisher

LOGGER = logging.getLogger("recommend-radio.dialogue-tasks")
TASK_WORKERS = 4
DISCOVERY_WATCHERS = 2
DISCOVERY_POLL_SECONDS = 0.75
DISCOVERY_MAX_POLLS = 40


class DialogueTaskService:
    """Run dialogue work off-request and publish durable progress events."""

    def __init__(self, publisher: SSEEventPublisher) -> None:
        self.publisher = publisher
        self.executor = ThreadPoolExecutor(
            max_workers=TASK_WORKERS,
            thread_name_prefix="dialogue-task",
        )
        self.discovery_executor = ThreadPoolExecutor(
            max_workers=DISCOVERY_WATCHERS,
            thread_name_prefix="discovery-watch",
        )

    def submit(
        self,
        *,
        service: Any,
        user_id: str,
        message: str,
        session_id: str | None,
        context_card_id: str | None,
        context_track_id: str | None,
        request_runner: Callable[[Callable[[], None]], Callable[[], None]],
    ) -> dict[str, str]:
        normalized = str(message or "").strip()
        if not normalized:
            raise ValueError("message is required")
        resolved_session_id = service.resolve_session_id(session_id=session_id)
        task_id = f"dialogue:{uuid4().hex}"
        self.publisher.publish(
            task_id=task_id,
            session_id=resolved_session_id,
            user_id=user_id,
            event_type="task",
            status="queued",
            payload={"status": "queued"},
        )

        def execute() -> None:
            self._run(
                service=service,
                user_id=user_id,
                task_id=task_id,
                session_id=resolved_session_id,
                message=normalized,
                context_card_id=context_card_id,
                context_track_id=context_track_id,
            )

        self.executor.submit(request_runner(execute))
        return {"taskId": task_id, "sessionId": resolved_session_id, "status": "queued"}

    def _run(
        self,
        *,
        service: Any,
        user_id: str,
        task_id: str,
        session_id: str,
        message: str,
        context_card_id: str | None,
        context_track_id: str | None,
    ) -> None:
        def publish(event_type: str, payload: dict[str, Any], status: str = "running") -> None:
            self.publisher.publish(
                task_id=task_id,
                session_id=session_id,
                user_id=user_id,
                event_type=event_type,
                status=status,
                payload=payload,
            )
        try:
            publish("progress", {"stage": "routing", "label": "正在理解你的需求"})
            result = service.send_message(
                message,
                session_id=session_id,
                context_card_id=context_card_id,
                context_track_id=context_track_id,
                progress=lambda stage, payload: publish(
                    "progress",
                    {"stage": stage, **dict(payload or {})},
                ),
            )
            publish("session", {"session": result})
            pending_cards = _pending_discovery_cards(result)
            if pending_cards:
                publish(
                    "discovery",
                    {"status": "running", "cardIds": [item["cardId"] for item in pending_cards]},
                )
                self.discovery_executor.submit(
                    self._watch_discovery,
                    service,
                    user_id,
                    task_id,
                    session_id,
                    result,
                )
            publish(
                "done",
                {"session": result, "discoveryPending": bool(pending_cards)},
                status="streaming" if pending_cards else "completed",
            )
        except Exception as exc:
            LOGGER.exception("dialogue task failed", extra={"task_id": task_id})
            publish(
                "error",
                {"message": str(exc)[:500]},
                status="failed",
            )

    def _watch_discovery(
        self,
        service: Any,
        user_id: str,
        task_id: str,
        session_id: str,
        initial: dict[str, Any],
    ) -> None:
        current = initial
        fingerprints: dict[str, tuple[str, int]] = {}
        for _attempt in range(DISCOVERY_MAX_POLLS):
            pending = _pending_discovery_cards(current)
            if not pending:
                self.publisher.publish(
                    task_id=task_id,
                    session_id=session_id,
                    user_id=user_id,
                    event_type="discovery",
                    status="completed",
                    payload={"status": "completed", "session": current},
                )
                return
            time.sleep(DISCOVERY_POLL_SECONDS)
            for card in pending:
                card_id = str(card["cardId"])
                try:
                    refreshed = service.refresh_recommendation_card(card_id)
                except Exception as exc:
                    LOGGER.warning("discovery card refresh failed for %s: %s", card_id, exc)
                    continue
                refreshed_card = _card_by_id(refreshed, card_id)
                fingerprint = (
                    str((refreshed_card or {}).get("discoveryStatus") or ""),
                    len((refreshed_card or {}).get("recommendations") or []),
                )
                if fingerprints.get(card_id) != fingerprint:
                    fingerprints[card_id] = fingerprint
                    self.publisher.publish(
                        task_id=task_id,
                        session_id=session_id,
                        user_id=user_id,
                        event_type="session",
                        status="running",
                        payload={"session": refreshed},
                    )
                current = refreshed
        self.publisher.publish(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            event_type="discovery",
            status="timeout",
            payload={"status": "timeout", "session": current},
        )

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.discovery_executor.shutdown(wait=False, cancel_futures=True)
        self.publisher.close()


def _pending_discovery_cards(session: dict[str, Any]) -> list[dict[str, Any]]:
    cards = session.get("cards") if isinstance(session, dict) else []
    if not isinstance(cards, list):
        return []
    return [
        card
        for card in cards
        if isinstance(card, dict)
        and card.get("kind") == "recommendation_carousel"
        and card.get("discoveryJobId")
        and card.get("discoveryStatus") not in {"completed", "failed"}
        and len(card.get("recommendations") or []) < 8
    ]


def _card_by_id(session: dict[str, Any], card_id: str) -> dict[str, Any] | None:
    cards = session.get("cards") if isinstance(session, dict) else []
    for card in cards if isinstance(cards, list) else []:
        if isinstance(card, dict) and card.get("cardId") == card_id:
            return card
    return None
