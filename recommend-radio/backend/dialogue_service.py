from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from amem_bridge import record_music_behavior
from conversation_memory import ConversationMemoryService
from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from music_keyword_pool import (
    detect_emotion,
    has_negative_intent,
    has_positive_intent,
    is_recall_request,
    is_recommendation_request,
    match_topics,
    matched_artist_names,
    topic_phrase,
)
from profile_projector import _default_llm_client, _parse_json_object
from profile_statement_service import ProfileStatementService
from recommendation_service import RecommendationService
from request_spec import RequestInterpreter, RequestSpec

MAX_MESSAGE_LENGTH = 1000
SESSION_TURN_LIMIT = 60
VISIBLE_CARD_LIMIT = 12
RECALL_RESULT_LIMIT = 8
RECOMMENDATION_CARD_LIMIT = 8
CHAT_LLM_HISTORY_LIMIT = 10
INTENT_CHAT = "CHAT"
INTENT_GREETING = "GREETING"
INTENT_RECOMMEND = "RECOMMEND"
INTENT_PROFILE_UPDATE = "PROFILE_UPDATE"
INTENT_CONTROL = "CONTROL"

CONFIRM_ACTIONS = {"confirm", "accurate"}
REJECT_ACTIONS = {"reject", "inaccurate"}
VALID_ACTIONS = CONFIRM_ACTIONS | REJECT_ACTIONS | {"discuss", "later"}

GENERIC_PROBES = (
    {
        "kind": "interest_probe",
        "title": "今晚的听感",
        "prompt": "这轮想让我把歌放轻一点，还是保留一点律动感？",
        "statement": "用户希望当前推荐更偏向轻松、舒服、适合持续播放的音乐。",
        "topic": "轻松听感",
        "polarity": "positive",
    },
    {
        "kind": "avoid_probe",
        "title": "推荐边界",
        "prompt": "如果我连续推同一类歌，你希望我主动换口味吗？",
        "statement": "用户希望推荐列表减少同一类音乐连续出现。",
        "topic": "换口味",
        "polarity": "negative",
    },
    {
        "kind": "interest_probe",
        "title": "探索口味",
        "prompt": "要不要给你留一点没听过但气质接近的歌？",
        "statement": "用户愿意接受少量气质接近的新音乐探索。",
        "topic": "探索推荐",
        "polarity": "positive",
    },
)


@dataclass(frozen=True)
class ExtractedSignal:
    polarity: str
    topic: str
    statement: str
    confidence: float
    kind: str = "preference_hypothesis"
    commit_policy: str = "shadow"


@dataclass(frozen=True)
class DialogueRoute:
    tool: str
    reason: str
    signal: ExtractedSignal | None = None
    emotion: str = ""
    primary_intent: str = INTENT_CHAT
    intents: tuple[str, ...] = (INTENT_CHAT,)
    need_profile: bool = False
    need_memory: bool = False
    need_recommendation_search: bool = False
    control_action: str = ""
    request_spec: RequestSpec | None = None
    route_source: str = "rule"
    confidence: float = 1.0


class MusicDialogueService:
    def __init__(
        self,
        db_path: Path | str | None = None,
        user_id: str = LEGACY_OWNER_USER_ID,
        recommendation_service: RecommendationService | None = None,
        router_llm_client: Any | None = None,
    ) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.user_id = user_id
        init_db(self.db_path)
        self.recommendation_service = recommendation_service or RecommendationService(
            db_path=self.db_path,
            user_id=self.user_id,
        )
        self.router_llm_client = router_llm_client
        self.conversation_memory = ConversationMemoryService(str(self.db_path), user_id=self.user_id)

    def get_session(self, session_id: str | None = None) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            session = self._get_or_create_session(conn, session_id=session_id)
            return self._serialize_session(conn, session)

    def list_sessions(self, limit: int = 30) -> dict[str, Any]:
        bounded_limit = min(max(int(limit or 30), 1), 80)
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT s.*,
                       (
                           SELECT content
                           FROM agent_dialogue_turns t
                           WHERE t.session_id = s.session_id AND t.role = 'user'
                           ORDER BY t.id ASC
                           LIMIT 1
                       ) AS first_user_message,
                       (
                           SELECT content
                           FROM agent_dialogue_turns t
                           WHERE t.session_id = s.session_id
                           ORDER BY t.id DESC
                           LIMIT 1
                       ) AS latest_message,
                       (
                           SELECT COUNT(*)
                           FROM agent_dialogue_turns t
                           WHERE t.session_id = s.session_id
                       ) AS message_count
                FROM agent_dialogue_sessions s
                WHERE s.user_id = ?
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (self.user_id, bounded_limit),
            ).fetchall()
        return {"items": [_serialize_session_summary(row) for row in rows]}

    def create_session(self) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            session = self._create_session(conn)
            return self._serialize_session(conn, session)

    def undo_last_message(self, *, session_id: str | None = None) -> dict[str, Any]:
        with get_connection(self.db_path) as conn:
            session = self._get_or_create_session(conn, session_id=session_id)
            checkpoint = self._latest_checkpoint(conn, session["session_id"])
            if checkpoint is None:
                result = self._serialize_session(conn, session)
                result["undone"] = False
                result["message"] = "没有可撤回的上一条消息。"
                return result

            self._restore_checkpoint(conn, checkpoint)
            conn.execute(
                """
                DELETE FROM agent_dialogue_checkpoints
                WHERE session_id = ? AND user_id = ? AND created_at >= ?
                """,
                (session["session_id"], self.user_id, checkpoint["created_at"]),
            )
            restored_session = self._load_session(conn, session["session_id"])
            result = self._serialize_session(conn, restored_session)
            result["undone"] = True
            result["message"] = "已撤回上一轮对话。"
            return result

    def send_message(
        self,
        message: str,
        *,
        session_id: str | None = None,
        context_card_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_message(message)
        if not normalized:
            raise ValueError("message is required")
        if len(normalized) > MAX_MESSAGE_LENGTH:
            raise ValueError("message is too long")

        with get_connection(self.db_path) as conn:
            session = self._get_or_create_session(conn, session_id=session_id)
            context_card = self._load_card(conn, context_card_id) if context_card_id else None
            if context_card_id and context_card is None:
                raise KeyError(context_card_id)

            payload = {}
            if context_card is not None:
                payload["quotedContext"] = _card_context(context_card)
            self._save_checkpoint(conn, session["session_id"], reason="before_user_message")
            self._insert_turn(
                conn,
                session["session_id"],
                "user",
                normalized,
                card_id=context_card_id,
                payload=payload,
            )
            resolved_session_id = session["session_id"]

        self.conversation_memory.append(session_id=resolved_session_id, role="user", content=normalized)
        route = self._route_message(normalized, context_card, session_id=resolved_session_id)
        warm_topic = (
            route.signal.topic if route.signal else route.request_spec.primary_label if route.request_spec else route.emotion
        )
        self.conversation_memory.refresh_warm(session_id=resolved_session_id, topic=warm_topic)
        if route.signal is not None:
            self._record_conversation_signal(resolved_session_id, normalized, route.signal)

        if route.tool == "direct_chat":
            assistant_text = _direct_chat_reply(normalized)
            with get_connection(self.db_path) as conn:
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="chatting",
                    focus="闲聊",
                    pending_context={},
                )
                session = self._load_session(conn, resolved_session_id)
                return self._serialize_session(conn, session, include_analysis=False)

        if route.tool == "explain_recommendation":
            assistant_text = self._recommendation_explanation(normalized)
            with get_connection(self.db_path) as conn:
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="chatting",
                    focus="推荐原因",
                    pending_context={},
                )
                session = self._load_session(conn, resolved_session_id)
                return self._serialize_session(conn, session)

        if route.tool == "recall_memory":
            recall_query = _recall_query(normalized)
            tracks = self._recall_tracks(recall_query)
            assistant_text = _recall_reply(recall_query, tracks)
            with get_connection(self.db_path) as conn:
                card = self._insert_card(
                    conn,
                    resolved_session_id,
                    kind="memory_recall",
                    title="听过的歌",
                    prompt=assistant_text,
                    statement="",
                    topic=recall_query or "最近播放",
                    polarity="neutral",
                    source_text=normalized,
                    payload={
                        "tracks": tracks,
                        "note": "来自本地播放记录和曲库",
                    },
                )
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    card_id=card["card_id"],
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="serving",
                    focus=recall_query or "最近播放",
                    pending_context=_card_context(card),
                )
                session = self._load_session(conn, resolved_session_id)
                return self._serialize_session(conn, session)

        if route.tool == "control":
            assistant_text = _control_reply(route.control_action)
            with get_connection(self.db_path) as conn:
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="chatting",
                    focus="播放控制",
                    pending_context={},
                )
                session = self._load_session(conn, resolved_session_id)
                return self._serialize_session(conn, session)

        if route.tool == "profile_chat":
            write_result = self._submit_signal_safely(route.signal)
            analysis = self._safe_analysis()
            recent_context = self._recent_user_context(resolved_session_id)
            fallback_text = _profile_chat_reply(
                normalized,
                analysis=analysis,
                recent_context=recent_context,
            )
            chat_reply = self._generate_chat_reply(
                normalized,
                route=route,
                session_id=resolved_session_id,
                analysis=analysis,
                fallback_text=fallback_text,
            )
            assistant_text = chat_reply["text"]
            with get_connection(self.db_path) as conn:
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    payload=_route_payload(route, reply_engine=chat_reply["engine"]),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="chatting",
                    focus=route.signal.topic if route.signal else "口味聊天",
                    pending_context={},
                )
                session = self._load_session(conn, resolved_session_id)
                result = self._serialize_session(conn, session)
            if write_result is not None:
                result["memoryIds"] = write_result.get("memoryIds") or []
                result["eventId"] = write_result.get("eventId")
            return result

        if route.tool == "chat_with_signal" and route.signal is not None:
            signal = route.signal
            write_result = self._submit_signal_safely(signal)
            analysis = self._safe_analysis()
            fallback_text = _signal_reply(
                signal,
                profile_hint=_profile_hint(analysis),
                stored=write_result is not None,
            )
            chat_reply = self._generate_chat_reply(
                normalized,
                route=route,
                session_id=resolved_session_id,
                analysis=analysis,
                fallback_text=fallback_text,
            )
            assistant_text = chat_reply["text"]
            with get_connection(self.db_path) as conn:
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    payload=_route_payload(route, reply_engine=chat_reply["engine"]),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="chatting",
                    focus=signal.topic or "当前状态",
                    pending_context={},
                )
                session = self._load_session(conn, resolved_session_id)
                result = self._serialize_session(conn, session)
            if write_result is not None:
                result["memoryIds"] = write_result.get("memoryIds") or []
                result["eventId"] = write_result.get("eventId")
            return result

        if route.tool == "recommend_music":
            signal = route.signal
            write_result = self._submit_signal_safely(signal) if signal is not None else None
            request_spec = route.request_spec or RequestInterpreter().interpret(normalized)
            scene_memory_id = self.recommendation_service.remember_request(
                scene="conversation",
                request_spec=request_spec,
            )
            discovery_result = self._bootstrap_discovery(request_spec)
            discovery_job_id = discovery_result.get("traceId") or self._schedule_discovery(request_spec)
            recommendations = self._safe_recommendations(request_spec)
            title_topic = (
                signal.topic
                if signal
                else request_spec.primary_label or route.emotion or _topic_from_text(normalized) or "这轮"
            )
            memory_ids = [] if write_result is None else write_result.get("memoryIds") or []
            analysis = self._safe_analysis()
            assistant_text = _recommendation_reply(
                topic=title_topic,
                recommendation_count=len(recommendations),
                source_text=normalized,
                profile_hint=_profile_hint(analysis),
            )
            with get_connection(self.db_path) as conn:
                card = self._insert_card(
                    conn,
                    resolved_session_id,
                    kind="recommendation_carousel",
                    title=f"{title_topic} 推荐",
                    prompt=assistant_text,
                    statement=signal.statement if signal else "",
                    topic=title_topic,
                    polarity=signal.polarity if signal else "neutral",
                    source_text=normalized,
                    payload={
                        "recommendations": recommendations,
                        "memoryIds": memory_ids,
                        "eventId": None if write_result is None else write_result.get("eventId"),
                        "requestSpec": request_spec.to_dict(),
                        "sceneMemoryId": scene_memory_id,
                        "discoveryJobId": discovery_job_id,
                        "discovery": discovery_result,
                        "note": "按本轮请求范围和长期听歌记录推荐",
                    },
                )
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    card_id=card["card_id"],
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="serving",
                    focus=title_topic,
                    pending_context=_card_context(card),
                )
                session = self._load_session(conn, resolved_session_id)
                result = self._serialize_session(conn, session)
            if write_result is not None:
                result["memoryIds"] = write_result.get("memoryIds") or []
                result["eventId"] = write_result.get("eventId")
            return result

        if route.tool == "confirm_signal" and route.signal is not None:
            signal = route.signal
            with get_connection(self.db_path) as conn:
                card = self._insert_card(
                    conn,
                    resolved_session_id,
                    kind="pending_confirmation",
                    title="等你确认",
                    prompt=f"我先理解成：{signal.statement} 这样准吗？",
                    statement=signal.statement,
                    topic=signal.topic,
                    polarity=signal.polarity,
                    source_text=normalized,
                    payload={
                        "confidence": signal.confidence,
                        "signalKind": signal.kind,
                        "contextCardId": context_card_id,
                    },
                )
                assistant_text = (
                    f"这会明显影响长期推荐，我先按你原话理解成：{signal.statement} 准吗？"
                )
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    card_id=card["card_id"],
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="awaiting_confirmation",
                    focus=signal.topic or signal.polarity,
                    pending_context=_card_context(card),
                )
                session = self._load_session(conn, resolved_session_id)
                return self._serialize_session(conn, session)

        if route.tool == "profile_update" and route.signal is not None:
            signal = route.signal
            write_result = self._submit_signal_safely(signal)
            assistant_text = _profile_update_reply(signal, stored=write_result is not None)
            with get_connection(self.db_path) as conn:
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="chatting",
                    focus=signal.topic or "口味调整",
                    pending_context={},
                )
                session = self._load_session(conn, resolved_session_id)
                result = self._serialize_session(conn, session)
            if write_result is not None:
                result["memoryIds"] = write_result.get("memoryIds") or []
                result["eventId"] = write_result.get("eventId")
            return result

        if route.signal is not None:
            signal = route.signal
            write_result = self._submit_signal_safely(signal)
            assistant_text = _signal_reply(
                signal,
                profile_hint=_profile_hint(self._safe_analysis()),
                stored=write_result is not None,
            )
            with get_connection(self.db_path) as conn:
                self._insert_turn(
                    conn,
                    resolved_session_id,
                    "assistant",
                    assistant_text,
                    payload=_route_payload(route),
                )
                self._touch_session(
                    conn,
                    resolved_session_id,
                    state="chatting",
                    focus=signal.topic or "当前状态",
                    pending_context={},
                )
                session = self._load_session(conn, resolved_session_id)
                result = self._serialize_session(conn, session)
            if write_result is not None:
                result["memoryIds"] = write_result.get("memoryIds") or []
                result["eventId"] = write_result.get("eventId")
            return result

        recent_context = self._recent_user_context(resolved_session_id)
        analysis = self._safe_analysis()
        fallback_text = _casual_reply(
            normalized,
            profile_hint=_profile_hint(analysis),
            recent_context=recent_context,
        )
        chat_reply = self._generate_chat_reply(
            normalized,
            route=route,
            session_id=resolved_session_id,
            analysis=analysis,
            fallback_text=fallback_text,
        )
        assistant_text = chat_reply["text"]
        with get_connection(self.db_path) as conn:
            self._insert_turn(
                conn,
                resolved_session_id,
                "assistant",
                assistant_text,
                payload=_route_payload(route, reply_engine=chat_reply["engine"]),
            )
            self._touch_session(
                conn,
                resolved_session_id,
                state="chatting",
                focus="日常聊天",
                pending_context={},
            )
            session = self._load_session(conn, resolved_session_id)
            return self._serialize_session(conn, session)

    def submit_feedback(
        self,
        card_id: str,
        action: str,
        *,
        reply: str | None = None,
    ) -> dict[str, Any]:
        normalized_action = _normalize_action(action)
        feedback_reply = _normalize_message(reply or "")
        card_snapshot: dict[str, Any]

        with get_connection(self.db_path) as conn:
            card = self._load_card(conn, card_id)
            if card is None:
                raise KeyError(card_id)
            session = self._load_session(conn, card["session_id"])
            if session is None or session["user_id"] != self.user_id:
                raise KeyError(card_id)

            next_status = _status_for_action(normalized_action)
            payload = _json_loads(card["payload_json"])
            payload["lastAction"] = normalized_action
            if feedback_reply:
                payload["feedbackReply"] = feedback_reply
            self._update_card(
                conn,
                card_id,
                status=next_status,
                payload=payload,
            )
            self._touch_session(
                conn,
                card["session_id"],
                state="writing_profile" if normalized_action in CONFIRM_ACTIONS else next_status,
                focus=card["topic"] or card["polarity"],
                pending_context=_card_context(card) if normalized_action == "discuss" else {},
            )
            card_snapshot = dict(card)
            card_snapshot["status"] = next_status
            card_snapshot["payload_json"] = json.dumps(payload, ensure_ascii=False)

        write_result: dict[str, Any] | None = None
        if normalized_action in CONFIRM_ACTIONS and card_snapshot["statement"].strip():
            try:
                write_result = self._submit_confirmed_statement(card_snapshot["statement"])
            except Exception as exc:
                with get_connection(self.db_path) as conn:
                    payload = _json_loads(card_snapshot["payload_json"])
                    payload["error"] = str(exc)
                    self._update_card(conn, card_id, status="failed", payload=payload)
                    session = self._load_session(conn, card_snapshot["session_id"])
                    return self._serialize_session(conn, session)

        with get_connection(self.db_path) as conn:
            card = self._load_card(conn, card_id)
            if card is None:
                raise KeyError(card_id)
            payload = _json_loads(card["payload_json"])
            if write_result is not None:
                payload["memoryIds"] = write_result.get("memoryIds") or []
                payload["eventId"] = write_result.get("eventId")
                self._update_card(conn, card_id, status="confirmed", payload=payload)
                card = self._load_card(conn, card_id)

            assistant_text = self._feedback_reply(card, normalized_action)
            turn_payload = (
                {"confirmedContext": _card_context(card)}
                if normalized_action in CONFIRM_ACTIONS
                else {}
            )
            self._insert_turn(
                conn,
                card["session_id"],
                "assistant",
                assistant_text,
                card_id=card_id,
                payload=turn_payload,
            )

            follow_up = None
            if normalized_action in CONFIRM_ACTIONS:
                follow_up = self._insert_follow_up_card(conn, card)
            elif normalized_action in REJECT_ACTIONS:
                follow_up = self._insert_probe_card(
                    conn,
                    card["session_id"],
                    source_text=card["source_text"],
                )
            elif normalized_action == "discuss" and feedback_reply:
                follow_up = self._insert_context_confirmation_card(conn, card, feedback_reply)

            self._touch_session(
                conn,
                card["session_id"],
                state=_state_after_action(normalized_action),
                focus=(follow_up or card)["topic"] or card["polarity"],
                pending_context=_card_context(follow_up or card),
            )
            session = self._load_session(conn, card["session_id"])
            result = self._serialize_session(conn, session)

        if write_result is not None:
            result["memoryIds"] = write_result.get("memoryIds") or []
            result["eventId"] = write_result.get("eventId")
            result["analysis"] = write_result.get("analysis")
        return result

    def _save_checkpoint(self, conn: Any, session_id: str, *, reason: str) -> None:
        session = self._load_session(conn, session_id)
        if session is None:
            return
        now = _utc_now()
        snapshot = {
            "session": dict(session),
            "turns": self._snapshot_rows(conn, "agent_dialogue_turns", session_id),
            "cards": self._snapshot_rows(conn, "agent_dialogue_cards", session_id),
            "signals": self._snapshot_rows(conn, "agent_dialogue_signals", session_id),
        }
        conn.execute(
            """
            INSERT INTO agent_dialogue_checkpoints (
                checkpoint_id, session_id, user_id, reason, snapshot_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"agent-dialogue-checkpoint:{uuid4().hex}",
                session_id,
                self.user_id,
                reason,
                json.dumps(snapshot, ensure_ascii=False),
                now,
            ),
        )

    def _latest_checkpoint(self, conn: Any, session_id: str) -> Any:
        return conn.execute(
            """
            SELECT *
            FROM agent_dialogue_checkpoints
            WHERE session_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id, self.user_id),
        ).fetchone()

    def _restore_checkpoint(self, conn: Any, checkpoint: Any) -> None:
        snapshot = _json_loads(checkpoint["snapshot_json"])
        session = snapshot.get("session") if isinstance(snapshot.get("session"), dict) else {}
        if session.get("session_id") != checkpoint["session_id"]:
            raise ValueError("checkpoint session mismatch")

        conn.execute(
            """
            UPDATE agent_dialogue_sessions
            SET state = ?, focus = ?, created_at = ?, updated_at = ?, pending_context_json = ?
            WHERE session_id = ? AND user_id = ?
            """,
            (
                session.get("state") or "chatting",
                session.get("focus") or "新的聊天",
                session.get("created_at") or checkpoint["created_at"],
                session.get("updated_at") or checkpoint["created_at"],
                session.get("pending_context_json") or "{}",
                checkpoint["session_id"],
                self.user_id,
            ),
        )

        for table in (
            "agent_dialogue_turns",
            "agent_dialogue_cards",
            "agent_dialogue_signals",
        ):
            conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (checkpoint["session_id"],))

        for row in _snapshot_list(snapshot, "turns"):
            conn.execute(
                """
                INSERT INTO agent_dialogue_turns (
                    id, session_id, role, content, card_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("id"),
                    row.get("session_id"),
                    row.get("role"),
                    row.get("content"),
                    row.get("card_id"),
                    row.get("payload_json") or "{}",
                    row.get("created_at"),
                ),
            )
        for row in _snapshot_list(snapshot, "cards"):
            conn.execute(
                """
                INSERT INTO agent_dialogue_cards (
                    card_id, session_id, kind, status, title, prompt, statement,
                    topic, polarity, source_text, payload_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("card_id"),
                    row.get("session_id"),
                    row.get("kind"),
                    row.get("status"),
                    row.get("title"),
                    row.get("prompt"),
                    row.get("statement"),
                    row.get("topic"),
                    row.get("polarity"),
                    row.get("source_text"),
                    row.get("payload_json") or "{}",
                    row.get("created_at"),
                    row.get("updated_at"),
                ),
            )
        for row in _snapshot_list(snapshot, "signals"):
            conn.execute(
                """
                INSERT INTO agent_dialogue_signals (
                    id, session_id, user_id, kind, topic, statement,
                    confidence, status, source_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("id"),
                    row.get("session_id"),
                    row.get("user_id"),
                    row.get("kind"),
                    row.get("topic"),
                    row.get("statement"),
                    row.get("confidence"),
                    row.get("status"),
                    row.get("source_text"),
                    row.get("created_at"),
                ),
            )

    def _snapshot_rows(self, conn: Any, table: str, session_id: str) -> list[dict[str, Any]]:
        order_column = "id" if table != "agent_dialogue_cards" else "created_at"
        rows = conn.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE session_id = ?
            ORDER BY {order_column} ASC
            """,
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _get_or_create_session(self, conn: Any, *, session_id: str | None) -> Any:
        session = self._load_session(conn, session_id) if session_id else self._latest_session(conn)
        if session is not None:
            return session
        return self._create_session(conn)

    def _latest_session(self, conn: Any) -> Any:
        return conn.execute(
            """
            SELECT *
            FROM agent_dialogue_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (self.user_id,),
        ).fetchone()

    def _load_session(self, conn: Any, session_id: str | None) -> Any:
        if not session_id:
            return None
        return conn.execute(
            """
            SELECT *
            FROM agent_dialogue_sessions
            WHERE session_id = ? AND user_id = ?
            """,
            (session_id, self.user_id),
        ).fetchone()

    def _create_session(self, conn: Any) -> Any:
        now = _utc_now()
        session_id = f"agent-dialogue:{uuid4().hex}"
        conn.execute(
            """
            INSERT INTO agent_dialogue_sessions (
                session_id, user_id, state, focus, created_at, updated_at, pending_context_json
            )
            VALUES (?, ?, 'probing', 'onboarding', ?, ?, '{}')
            """,
            (session_id, self.user_id, now, now),
        )
        self._insert_turn(
            conn,
            session_id,
            "assistant",
            "你好，我在。",
        )
        self._touch_session(
            conn,
            session_id,
            state="chatting",
            focus="新的聊天",
            pending_context={},
        )
        return self._load_session(conn, session_id)

    def _insert_seed_card(self, conn: Any, session_id: str) -> dict[str, Any]:
        analysis = self._safe_analysis()
        top_positive = (analysis.get("summary") or {}).get("topPositiveTopics") or []
        if top_positive:
            topic = str(top_positive[0].get("name") or "").strip()
            if topic:
                return self._insert_card(
                    conn,
                    session_id,
                    kind="interest_probe",
                    title="延续这个口味？",
                    prompt=f"我看你之前和 {topic} 比较合拍，这轮还按这个方向走吗？",
                    statement=f"用户希望 B 站电台继续优先推荐 {topic} 相关音乐。",
                    topic=topic,
                    polarity="positive",
                    source_text=f"profile:{topic}",
                    payload={"seed": "profile"},
                )
        return self._insert_probe_card(conn, session_id, source_text="onboarding")

    def _insert_probe_card(self, conn: Any, session_id: str, *, source_text: str) -> dict[str, Any]:
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_dialogue_cards WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
        probe = GENERIC_PROBES[count % len(GENERIC_PROBES)]
        return self._insert_card(
            conn,
            session_id,
            kind=probe["kind"],
            title=probe["title"],
            prompt=probe["prompt"],
            statement=probe["statement"],
            topic=probe["topic"],
            polarity=probe["polarity"],
            source_text=source_text,
            payload={"seed": "generic"},
        )

    def _insert_recommendation_follow_up(
        self,
        conn: Any,
        session_id: str,
        topic: str,
    ) -> dict[str, Any]:
        return self._insert_card(
            conn,
            session_id,
            kind="interest_probe",
            title="继续校准",
            prompt="这轮如果方向对，我下次就多保留这种气质；如果不对，直接告诉我哪里偏了。",
            statement=f"用户希望继续保留 {topic} 这一类音乐气质。",
            topic=topic,
            polarity="positive",
            source_text=f"recommendation:{topic}",
            payload={"seed": "recommendation_follow_up"},
        )

    def _insert_context_confirmation_card(
        self,
        conn: Any,
        source_card: Any,
        reply: str,
    ) -> dict[str, Any]:
        statement = f"{source_card['statement'].strip()} 补充：{reply}"
        return self._insert_card(
            conn,
            source_card["session_id"],
            kind="pending_confirmation",
            title="待确认补充",
            prompt=f"我把补充合并成：{statement} 准吗？",
            statement=statement,
            topic=source_card["topic"],
            polarity=source_card["polarity"],
            source_text=source_card["statement"],
            payload={"parentCardId": source_card["card_id"]},
        )

    def _insert_follow_up_card(self, conn: Any, source_card: Any) -> dict[str, Any]:
        if source_card["polarity"] == "negative":
            return self._insert_card(
                conn,
                source_card["session_id"],
                kind="interest_probe",
                title="替代方向",
                prompt="要不要把被你避开的方向替换成旋律更清晰、情绪更稳定的内容？",
                statement="用户希望用旋律更清晰、情绪更稳定的内容替代明确回避的音乐方向。",
                topic="替代方向",
                polarity="positive",
                source_text=source_card["statement"],
                payload={"parentCardId": source_card["card_id"]},
            )
        return self._insert_card(
            conn,
            source_card["session_id"],
            kind="avoid_probe",
            title="边界控制",
            prompt="就算喜欢这个方向，也避免同一 UP 主或同质内容连续刷屏吗？",
            statement="用户不希望推荐列表里连续出现同一 UP 主或高度同质的音乐内容。",
            topic="推荐边界",
            polarity="negative",
            source_text=source_card["statement"],
            payload={"parentCardId": source_card["card_id"]},
        )

    def _record_conversation_signal(
        self,
        session_id: str,
        source_text: str,
        signal: ExtractedSignal,
    ) -> None:
        now = _utc_now()
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO agent_dialogue_signals (
                    session_id, user_id, kind, topic, statement,
                    confidence, status, source_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    self.user_id,
                    signal.kind,
                    signal.topic[:80],
                    signal.statement[:1000],
                    signal.confidence,
                    signal.commit_policy,
                    source_text[:1000],
                    now,
                ),
            )
        try:
            record_music_behavior(
                self.recommendation_service.amem_bridge,
                user_id=self.user_id,
                event="conversation_signal",
                scene="conversation",
                payload={
                    "sessionId": session_id,
                    "kind": signal.kind,
                    "topic": signal.topic,
                    "statement": signal.statement,
                    "confidence": signal.confidence,
                    "status": signal.commit_policy,
                    "sourceText": source_text,
                },
            )
        except Exception:
            return

    def _submit_signal_safely(self, signal: ExtractedSignal | None) -> dict[str, Any] | None:
        if signal is None or signal.commit_policy != "commit":
            return None
        if signal.kind == "preference_hypothesis" and signal.confidence < 0.82:
            return None
        try:
            return self._submit_confirmed_statement(signal.statement)
        except Exception:
            return None

    def _recent_user_context(self, session_id: str, limit: int = 4) -> list[str]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT content
                FROM agent_dialogue_turns
                WHERE session_id = ? AND role = 'user'
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [str(row["content"]) for row in reversed(rows)]

    def _recent_turn_context(self, session_id: str, limit: int = CHAT_LLM_HISTORY_LIMIT) -> list[dict[str, str]]:
        bounded_limit = min(max(int(limit or CHAT_LLM_HISTORY_LIMIT), 2), 20)
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM agent_dialogue_turns
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, bounded_limit),
            ).fetchall()
        return [
            {"role": str(row["role"] or ""), "content": str(row["content"] or "")}
            for row in reversed(rows)
            if str(row["content"] or "").strip()
        ]

    def _generate_chat_reply(
        self,
        message: str,
        *,
        route: DialogueRoute,
        session_id: str,
        analysis: dict[str, Any],
        fallback_text: str,
    ) -> dict[str, str]:
        if not _dialogue_llm_enabled():
            return {"text": fallback_text, "engine": "fallback"}
        try:
            text = _llm_chat_reply(
                message,
                route=route,
                analysis=analysis,
                recent_turns=self._recent_turn_context(session_id),
            )
            return {"text": text, "engine": "llm"}
        except Exception:
            return {"text": fallback_text, "engine": "fallback"}

    def _recommendation_explanation(self, message: str) -> str:
        trace = self._latest_recommendation_trace()
        if not trace.get("available"):
            return (
                "我还没有稳定的一轮推荐记录。你可以先说今天想听什么，"
                "我会按你的原话、最近播放和负反馈一起选。"
            )

        final_results = trace.get("finalResults") or []
        target_index = _explanation_target_index(message, len(final_results))
        if target_index is not None:
            return _single_recommendation_explanation(final_results[target_index], target_index)

        matched = _trace_matched_preferences(final_results)
        penalties = _trace_penalty_labels(final_results)
        sources = _trace_source_labels(final_results)
        evidence = _trace_evidence(final_results)

        parts = ["这批不是重新猜出来的，是按上一轮推荐留下的排序记录解释。"]
        if matched:
            parts.append(f"主方向贴着 {'、'.join(matched[:4])}。")
        elif evidence:
            parts.append(f"主要依据是{evidence[0]}。")
        if "探索" in sources:
            parts.append("里面留了少量探索项，用来试探相近但不完全重复的口味。")
        if penalties:
            parts.append(f"同时实际做过这些压低处理：{'、'.join(penalties[:3])}。")
        parts.append("如果你想看单首原因，可以直接问“第一首为什么”。")
        return "".join(parts)

    def _latest_recommendation_trace(self) -> dict[str, Any]:
        for scene in ("conversation", "home"):
            try:
                trace = self.recommendation_service.latest_debug_trace(scene=scene)
            except Exception:
                continue
            if trace.get("available"):
                return trace
        return {"available": False}

    def _safe_recommendations(self, request_spec: RequestSpec | None = None) -> list[dict[str, Any]]:
        try:
            result = self.recommendation_service.list_recommendations(
                scene="conversation",
                limit=RECOMMENDATION_CARD_LIMIT,
                request_spec=request_spec,
            )
        except Exception:
            return []
        items = result.get("items") if isinstance(result, dict) else []
        return items if isinstance(items, list) else []

    def _schedule_discovery(self, request_spec: RequestSpec) -> str | None:
        try:
            return self.recommendation_service.enqueue_discovery(
                scene="conversation",
                limit=RECOMMENDATION_CARD_LIMIT,
                request_spec=request_spec,
            )
        except Exception:
            return None

    def _bootstrap_discovery(self, request_spec: RequestSpec) -> dict[str, Any]:
        try:
            return self.recommendation_service.bootstrap_discovery(
                scene="conversation",
                limit=RECOMMENDATION_CARD_LIMIT,
                request_spec=request_spec,
            )
        except Exception:
            return {}

    def _recall_tracks(self, query: str) -> list[dict[str, Any]]:
        pattern = f"%{_escape_like(query)}%" if query else "%"
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT t.*,
                       COALESCE(
                           r.last_played_at,
                           pr.last_played_at,
                           t.updated_at
                       ) AS last_played_at,
                       COALESCE(r.play_count, 0) AS recent_play_count,
                       COALESCE(r.position_ms, pr.position_ms, 0) AS position_ms,
                       COALESCE(r.listen_ms, pr.listen_ms, 0) AS listen_ms,
                       COALESCE(r.completed, pr.completed, 0) AS completed
                FROM tracks t
                LEFT JOIN recent r ON r.user_id = ? AND r.track_id = t.track_id
                LEFT JOIN playback_recent pr ON pr.user_id = ? AND pr.track_id = t.track_id
                WHERE (r.track_id IS NOT NULL OR pr.track_id IS NOT NULL)
                  AND (
                    ? = ''
                    OR t.title LIKE ? ESCAPE '\\'
                    OR t.owner LIKE ? ESCAPE '\\'
                    OR COALESCE(t.page_title, '') LIKE ? ESCAPE '\\'
                  )
                ORDER BY COALESCE(r.last_played_at, pr.last_played_at, t.updated_at) DESC
                LIMIT ?
                """,
                (
                    self.user_id,
                    self.user_id,
                    query,
                    pattern,
                    pattern,
                    pattern,
                    RECALL_RESULT_LIMIT,
                ),
            ).fetchall()
        return [self.recommendation_service.library._track_payload_with_meta(row) for row in rows]

    def _insert_card(
        self,
        conn: Any,
        session_id: str,
        *,
        kind: str,
        title: str,
        prompt: str,
        statement: str,
        topic: str,
        polarity: str,
        source_text: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        card_id = f"agent-card:{uuid4().hex}"
        conn.execute(
            """
            INSERT INTO agent_dialogue_cards (
                card_id, session_id, kind, status, title, prompt, statement,
                topic, polarity, source_text, payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card_id,
                session_id,
                kind,
                title[:80],
                prompt[:500],
                statement[:1000],
                topic[:80],
                polarity,
                source_text[:1000],
                json.dumps(payload or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        return {
            "card_id": card_id,
            "session_id": session_id,
            "kind": kind,
            "status": "pending",
            "title": title,
            "prompt": prompt,
            "statement": statement,
            "topic": topic,
            "polarity": polarity,
            "source_text": source_text,
            "payload_json": json.dumps(payload or {}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        }

    def _load_card(self, conn: Any, card_id: str | None) -> Any:
        if not card_id:
            return None
        return conn.execute(
            """
            SELECT c.*
            FROM agent_dialogue_cards c
            JOIN agent_dialogue_sessions s ON s.session_id = c.session_id
            WHERE c.card_id = ? AND s.user_id = ?
            """,
            (card_id, self.user_id),
        ).fetchone()

    def _update_card(
        self,
        conn: Any,
        card_id: str,
        *,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            UPDATE agent_dialogue_cards
            SET status = ?, payload_json = ?, updated_at = ?
            WHERE card_id = ?
            """,
            (status, json.dumps(payload, ensure_ascii=False), _utc_now(), card_id),
        )

    def _insert_turn(
        self,
        conn: Any,
        session_id: str,
        role: str,
        content: str,
        *,
        card_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO agent_dialogue_turns (
                session_id, role, content, card_id, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                content[:2000],
                card_id,
                json.dumps(payload or {}, ensure_ascii=False),
                _utc_now(),
            ),
        )

    def _touch_session(
        self,
        conn: Any,
        session_id: str,
        *,
        state: str,
        focus: str,
        pending_context: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            UPDATE agent_dialogue_sessions
            SET state = ?, focus = ?, pending_context_json = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                state,
                (focus or "general")[:80],
                json.dumps(pending_context, ensure_ascii=False),
                _utc_now(),
                session_id,
            ),
        )

    def _serialize_session(
        self,
        conn: Any,
        session: Any,
        *,
        include_analysis: bool = True,
    ) -> dict[str, Any]:
        if session is None:
            return {}
        turns = conn.execute(
            """
            SELECT *
            FROM agent_dialogue_turns
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session["session_id"], SESSION_TURN_LIMIT),
        ).fetchall()
        card_rows = conn.execute(
            """
            SELECT *
            FROM agent_dialogue_cards
            WHERE session_id = ?
            ORDER BY updated_at DESC
            """,
            (session["session_id"],),
        ).fetchall()
        cards = [_serialize_card(row) for row in card_rows]
        cards_by_id = {card["cardId"]: card for card in cards}
        return {
            "sessionId": session["session_id"],
            "state": session["state"],
            "focus": session["focus"],
            "createdAt": session["created_at"],
            "updatedAt": session["updated_at"],
            "pendingContext": _json_loads(session["pending_context_json"]),
            "messages": [_serialize_turn(row, cards_by_id) for row in reversed(turns)],
            "cards": self._pending_cards(conn, session["session_id"]),
            "analysis": self._safe_analysis() if include_analysis else _empty_analysis(),
        }

    def _pending_cards(self, conn: Any, session_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM agent_dialogue_cards
            WHERE session_id = ?
            ORDER BY
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'discussing' THEN 1
                    WHEN 'failed' THEN 2
                    WHEN 'deferred' THEN 3
                    ELSE 4
                END,
                updated_at DESC
            LIMIT ?
            """,
            (session_id, VISIBLE_CARD_LIMIT),
        ).fetchall()
        return [_serialize_card(row) for row in rows]

    def _feedback_reply(self, card: Any, action: str) -> str:
        statement = card["statement"].strip()
        if action in CONFIRM_ACTIONS:
            return f"收到，这条后续会影响选歌：{statement}"
        if action in REJECT_ACTIONS:
            return "好，这条判断不采用。下一轮我换个角度。"
        if action == "later":
            return "好，先搁置，不影响后续推荐。"
        return f"我们继续聊这条：{statement}"

    def _submit_confirmed_statement(self, statement: str) -> dict[str, Any]:
        return self.recommendation_service.submit_profile_statement(statement)

    def _safe_analysis(self) -> dict[str, Any]:
        try:
            return self.recommendation_service.music_profile_analysis(scene="conversation")
        except Exception:
            return _empty_analysis()

    def _route_message(self, message: str, context_card: Any | None, *, session_id: str) -> DialogueRoute:
        rule_route = _route_message(message, context_card)
        if _is_high_confidence_route(rule_route, message):
            return _canonical_route(rule_route, message, source="rule", confidence=1.0)
        if not _dialogue_router_llm_enabled():
            return _canonical_route(rule_route, message, source="rule_fallback", confidence=0.0)
        try:
            route = _llm_route_message(
                message,
                context_card=context_card,
                recent_turns=self.conversation_memory.hot(session_id=session_id),
                client=self.router_llm_client or _default_llm_client(),
            )
            return _canonical_route(route, message, source="llm_tool", confidence=0.72)
        except Exception:
            return _canonical_route(rule_route, message, source="rule_fallback", confidence=0.0)


def _serialize_session_summary(row: Any) -> dict[str, Any]:
    first_user_message = str(row["first_user_message"] or "").strip()
    focus = str(row["focus"] or "").strip()
    title = first_user_message or focus or "新的聊天"
    latest_message = str(row["latest_message"] or "").strip()
    return {
        "sessionId": row["session_id"],
        "title": _compact_text(title, 28),
        "preview": _compact_text(latest_message or title, 56),
        "state": row["state"],
        "focus": focus,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "messageCount": int(row["message_count"] or 0),
    }


def _serialize_turn(row: Any, cards_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = _json_loads(row["payload_json"])
    card_id = row["card_id"]
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "content": row["content"],
        "cardId": card_id,
        "createdAt": row["created_at"],
        "quotedContext": payload.get("quotedContext"),
        "confirmedContext": payload.get("confirmedContext"),
        "card": cards_by_id.get(card_id) if card_id else None,
    }


def _serialize_card(row: Any) -> dict[str, Any]:
    payload = _json_loads(row["payload_json"])
    return {
        "cardId": row["card_id"],
        "kind": row["kind"],
        "status": row["status"],
        "title": row["title"],
        "prompt": row["prompt"],
        "statement": row["statement"],
        "topic": row["topic"],
        "polarity": row["polarity"],
        "sourceText": row["source_text"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "actions": _actions_for_kind(row["kind"]),
        "memoryIds": payload.get("memoryIds") or [],
        "eventId": payload.get("eventId"),
        "error": payload.get("error"),
        "note": payload.get("note") or "",
        "requestSpec": payload.get("requestSpec") or {},
        "sceneMemoryId": payload.get("sceneMemoryId"),
        "discoveryJobId": payload.get("discoveryJobId"),
        "recommendations": payload.get("recommendations") or [],
        "tracks": payload.get("tracks") or [],
    }


def _actions_for_kind(kind: str) -> list[str]:
    if kind == "pending_confirmation":
        return ["accurate", "inaccurate", "discuss"]
    if kind in {"recommendation_carousel", "memory_recall"}:
        return []
    return ["confirm", "reject", "discuss", "later"]


def _card_context(card: Any) -> dict[str, Any]:
    return {
        "cardId": card["card_id"],
        "kind": card["kind"],
        "statement": card["statement"],
        "sourceText": card["source_text"] or card["statement"],
        "topic": card["topic"],
        "polarity": card["polarity"],
    }


def _route_payload(route: DialogueRoute, *, reply_engine: str | None = None) -> dict[str, Any]:
    template = _route_template(route)
    payload = {
        "route": route.tool,
        "routeReason": route.reason,
        "intent": route.primary_intent,
        "intents": list(route.intents),
        "needProfile": route.need_profile,
        "needMemory": route.need_memory,
        "needRecommendationSearch": route.need_recommendation_search,
        "routeTemplate": template,
    }
    if route.control_action:
        payload["controlAction"] = route.control_action
    if reply_engine:
        payload["replyEngine"] = reply_engine
    return payload


def _route_template(route: DialogueRoute) -> dict[str, Any]:
    return {
        "schemaVersion": "dialogue-route/v1",
        "tool": route.tool,
        "intent": route.primary_intent,
        "intents": list(route.intents),
        "needProfile": route.need_profile,
        "needMemory": route.need_memory,
        "needRecommendationSearch": route.need_recommendation_search,
        "controlAction": route.control_action or None,
        "emotion": route.emotion or None,
        "signal": None
        if route.signal is None
        else {
            "polarity": route.signal.polarity,
            "topic": route.signal.topic,
            "statement": route.signal.statement,
            "confidence": route.signal.confidence,
            "kind": route.signal.kind,
            "commitPolicy": route.signal.commit_policy,
        },
        "requestSpec": (route.request_spec or RequestSpec()).to_dict(),
        "source": route.route_source,
        "confidence": round(route.confidence, 3),
    }


def _canonical_route(route: DialogueRoute, message: str, *, source: str, confidence: float) -> DialogueRoute:
    return replace(
        route,
        request_spec=RequestInterpreter().interpret(message),
        route_source=source,
        confidence=confidence,
    )


def _is_high_confidence_route(route: DialogueRoute, message: str) -> bool:
    if route.tool in {"control", "direct_chat", "explain_recommendation", "recall_memory", "confirm_signal", "profile_update"}:
        return True
    if route.tool == "recommend_music":
        return _has_any(
            _normalize_message(message).casefold(),
            ("给我推荐", "推荐几首", "推几首", "来几首", "放几首", "来点", "歌单"),
        )
    return False


_ROUTER_TOOL_DEFAULTS: dict[str, dict[str, Any]] = {
    "recommend_music": {"intent": INTENT_RECOMMEND, "profile": True, "memory": True, "search": True},
    "profile_chat": {"intent": INTENT_CHAT, "profile": True, "memory": True, "search": False},
    "profile_update": {"intent": INTENT_PROFILE_UPDATE, "profile": True, "memory": False, "search": False},
    "chat_with_signal": {"intent": INTENT_CHAT, "profile": False, "memory": True, "search": False},
    "casual_chat": {"intent": INTENT_CHAT, "profile": True, "memory": True, "search": False},
    "explain_recommendation": {"intent": INTENT_CHAT, "profile": True, "memory": True, "search": False},
    "recall_memory": {"intent": INTENT_CHAT, "profile": False, "memory": True, "search": False},
}


def _llm_route_message(
    message: str,
    *,
    context_card: Any | None,
    recent_turns: list[dict[str, str]],
    client: Any,
) -> DialogueRoute:
    if not hasattr(client, "complete_tool"):
        raise ValueError("router LLM client does not support tool calls")
    system_prompt = (
        "You are a music dialogue router. Call exactly one route_dialogue tool. "
        "Never write a reply. Select recommend_music only when the user wants playable music now; "
        "select profile_update only for an explicit durable preference. A temporary request is not durable. "
        "Return a signal only when the message states a preference with a concrete topic."
    )
    tool = {
        "type": "function",
        "function": {
            "name": "route_dialogue",
            "description": "Return the normalized dialogue route template.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool"],
                "properties": {
                    "tool": {"type": "string", "enum": list(_ROUTER_TOOL_DEFAULTS)},
                    "emotion": {"type": "string", "maxLength": 32},
                    "signal": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["polarity", "topic", "kind", "commitPolicy"],
                        "properties": {
                            "polarity": {"type": "string", "enum": ["positive", "negative"]},
                            "topic": {"type": "string", "maxLength": 80},
                            "kind": {"type": "string", "enum": ["stable_preference", "preference_hypothesis", "recent_state"]},
                            "commitPolicy": {"type": "string", "enum": ["commit", "shadow", "confirm"]},
                        },
                    },
                },
            },
        },
    }
    response = client.complete_tool(
        system_prompt=system_prompt,
        user_prompt=json.dumps(
            {
                "message": message,
                "contextCard": _card_context(context_card) if context_card is not None else None,
                "recentTurns": _compact_recent_turns(recent_turns, limit=6),
            },
            ensure_ascii=False,
        ),
        tools=[tool],
    )
    if response.name != "route_dialogue":
        raise ValueError("router returned an unsupported tool name")
    args = response.arguments
    tool_name = str(args.get("tool") or "")
    defaults = _ROUTER_TOOL_DEFAULTS.get(tool_name)
    if defaults is None:
        raise ValueError("router returned an unsupported route")
    signal = _signal_from_tool_args(args.get("signal"), message)
    return DialogueRoute(
        tool=tool_name,
        reason="LLM tool route",
        signal=signal,
        emotion=str(args.get("emotion") or "")[:32],
        primary_intent=defaults["intent"],
        intents=_route_intents(defaults["intent"], signal),
        need_profile=defaults["profile"],
        need_memory=defaults["memory"],
        need_recommendation_search=defaults["search"],
    )


def _signal_from_tool_args(value: Any, message: str) -> ExtractedSignal | None:
    if not isinstance(value, dict):
        return None
    topic = _clean_topic(str(value.get("topic") or ""))
    polarity = str(value.get("polarity") or "")
    kind = str(value.get("kind") or "")
    policy = str(value.get("commitPolicy") or "")
    if not topic or polarity not in {"positive", "negative"} or kind not in {"stable_preference", "preference_hypothesis", "recent_state"} or policy not in {"commit", "shadow", "confirm"}:
        return None
    return ExtractedSignal(
        polarity=polarity,
        topic=topic,
        statement=_preference_statement(topic, message, kind),
        confidence=0.72,
        kind=kind,
        commit_policy=policy,
    )


def _route_message(message: str, context_card: Any | None) -> DialogueRoute:
    text = _normalize_message(message)
    control_action = _control_action(text)
    if control_action:
        return DialogueRoute(
            "control",
            "user sends playback control command",
            signal=None,
            emotion="",
            primary_intent=INTENT_CONTROL,
            intents=(INTENT_CONTROL,),
            control_action=control_action,
        )
    if _is_direct_chat(text):
        return DialogueRoute(
            "direct_chat",
            "low-information chat should not touch profile or recommendation tools",
            signal=None,
            emotion="",
            primary_intent=INTENT_GREETING,
            intents=(INTENT_GREETING,),
            need_profile=False,
            need_memory=False,
            need_recommendation_search=False,
        )

    signal = _extract_signal(text, context_card) or _extract_scene_signal(text)
    emotion = detect_emotion(text)
    if _is_recommendation_explanation_request(text):
        return DialogueRoute(
            "explain_recommendation",
            "user asks why these songs were chosen",
            signal=signal,
            emotion=emotion,
            primary_intent=INTENT_CHAT,
            intents=_route_intents(INTENT_CHAT, signal),
            need_profile=True,
            need_memory=True,
        )
    if is_recall_request(text):
        return DialogueRoute(
            "recall_memory",
            "user asks about previous listening memory",
            signal=signal,
            emotion=emotion,
            primary_intent=INTENT_CHAT,
            intents=_route_intents(INTENT_CHAT, signal),
            need_memory=True,
        )
    if _is_profile_chat_request(text):
        return DialogueRoute(
            "profile_chat",
            "user discusses taste without asking for playable candidates",
            signal,
            emotion,
            primary_intent=INTENT_CHAT,
            intents=_route_intents(INTENT_CHAT, signal),
            need_profile=True,
            need_memory=True,
            need_recommendation_search=False,
        )
    if _is_recommendation_execution_request(text):
        return DialogueRoute(
            "recommend_music",
            "user asks to generate playable recommendation candidates",
            signal,
            emotion,
            primary_intent=INTENT_RECOMMEND,
            intents=_route_intents(INTENT_RECOMMEND, signal),
            need_profile=True,
            need_memory=True,
            need_recommendation_search=True,
        )
    if signal is not None and signal.commit_policy == "confirm":
        return DialogueRoute(
            "confirm_signal",
            "signal needs explicit confirmation",
            signal,
            emotion,
            primary_intent=INTENT_PROFILE_UPDATE,
            intents=(INTENT_PROFILE_UPDATE,),
            need_profile=True,
        )
    if signal is not None and _is_explicit_profile_update(text):
        return DialogueRoute(
            "profile_update",
            "user explicitly updates music preference",
            signal,
            emotion,
            primary_intent=INTENT_PROFILE_UPDATE,
            intents=(INTENT_PROFILE_UPDATE,),
            need_profile=True,
        )
    if signal is not None:
        return DialogueRoute(
            "chat_with_signal",
            "conversation contains usable context",
            signal,
            emotion,
            primary_intent=INTENT_CHAT,
            intents=(INTENT_CHAT,),
            need_memory=True,
        )
    return DialogueRoute(
        "casual_chat",
        "general music companion chat",
        None,
        emotion,
        primary_intent=INTENT_CHAT,
        intents=(INTENT_CHAT,),
        need_profile=True,
        need_memory=True,
    )


def _route_intents(primary: str, signal: ExtractedSignal | None) -> tuple[str, ...]:
    intents = [primary]
    if (
        signal is not None
        and signal.kind != "recent_state"
        and primary != INTENT_PROFILE_UPDATE
    ):
        intents.append(INTENT_PROFILE_UPDATE)
    return tuple(dict.fromkeys(intents))


def _control_action(text: str) -> str:
    compact = re.sub(r"[\s，,。.!！?？；;、~～]", "", _normalize_message(text).casefold())
    if compact in {"暂停", "停一下", "先停", "先暂停", "别放了", "pause"}:
        return "pause"
    if compact in {"继续", "继续播放", "播放", "接着放", "resume"}:
        return "resume"
    if compact in {"下一首", "下一个", "换下一首", "next"}:
        return "next"
    if compact in {"上一首", "上一个", "previous", "prev"}:
        return "previous"
    return ""


def _is_direct_chat(text: str) -> bool:
    normalized = _normalize_message(text).casefold()
    compact = re.sub(r"[\s，,。.!！?？；;、~～]", "", normalized)
    if _is_greeting(normalized):
        return True
    return compact in {
        "哈哈",
        "哈哈哈",
        "谢谢",
        "谢谢你",
        "谢啦",
        "好的",
        "好",
        "ok",
        "收到",
        "晚安",
        "拜拜",
        "再见",
        "thanks",
        "thankyou",
        "bye",
        "hh",
        "hhh",
    }


def _is_profile_chat_request(text: str) -> bool:
    normalized = _normalize_message(text).casefold()
    if _asks_for_artist_names_only(normalized):
        return True
    discussion_words = (
        "你觉得",
        "觉得",
        "适合我",
        "符合我",
        "符合我的品味",
        "我的品味",
        "我的口味",
        "我喜欢什么",
        "我爱听什么",
        "哪种歌手",
        "那种歌手",
        "什么歌手",
        "哪个更适合",
        "是不是更喜欢",
        "更喜欢",
        "比较符合",
        "类似的歌手",
        "相近的歌手",
        "像谁",
    )
    if not _has_any(normalized, discussion_words):
        return False
    return _mentions_music_domain(normalized) or _has_any(normalized, ("品味", "口味"))


def _asks_for_artist_names_only(text: str) -> bool:
    if "歌手" not in text:
        return False
    if _has_any(text, ("几首", "首歌", "歌曲", "歌单", "播放", "放几首", "来几首")):
        return False
    return _has_any(text, ("推荐", "适合", "符合", "类似", "哪种", "什么", "几个", "哪些"))


def _is_recommendation_execution_request(text: str) -> bool:
    normalized = _normalize_message(text).casefold()
    if _is_profile_chat_request(normalized):
        return False
    execution_words = (
        "换一批",
        "再来一批",
        "下一批",
        "来一轮",
        "来一组",
        "给我推荐",
        "推荐 10",
        "推荐10",
        "推荐几首",
        "推几首",
        "来几首",
        "找几首",
        "放几首",
        "找点",
        "来点",
        "歌单",
        "现在就想听",
        "我现在想听",
        "今晚想听",
        "今天想听",
        "想听点",
        "想听一点",
    )
    if _has_any(normalized, execution_words):
        return True
    if is_recommendation_request(normalized) and not _has_any(
        normalized,
        ("为什么", "为啥", "原因", "你觉得", "是不是", "哪个", "什么歌手", "哪种歌手"),
    ):
        return True
    return False


def _is_explicit_profile_update(text: str) -> bool:
    normalized = _normalize_message(text).casefold()
    if _has_negative_preference_intent(normalized):
        return True
    if not _has_positive_preference_intent(normalized):
        return False
    return _has_any(
        normalized,
        (
            "我喜欢",
            "我爱听",
            "我很喜欢",
            "我超喜欢",
            "一直",
            "长期",
            "以后",
            "多推",
            "少推",
            "其实",
            "最近",
            "开始",
        ),
    )


def _has_negative_preference_intent(text: str) -> bool:
    normalized = _normalize_message(text).casefold()
    return has_negative_intent(normalized) or _has_any(
        normalized,
        (
            "少给我推",
            "少给我推荐",
            "别给我推",
            "别给我推荐",
            "不要给我推",
            "不要给我推荐",
            "以后少推",
            "以后别推",
        ),
    )


def _has_positive_preference_intent(text: str) -> bool:
    normalized = _normalize_message(text).casefold()
    return has_positive_intent(normalized) or _has_any(
        normalized,
        ("多给我推", "多给我推荐", "以后多推", "以后多推荐"),
    )


def _mentions_music_domain(text: str) -> bool:
    return bool(
        matched_artist_names(text)
        or match_topics(text)
        or _has_any(
            text,
            (
                "歌",
                "歌手",
                "音乐",
                "旋律",
                "风格",
                "rnb",
                "r&b",
                "华语",
                "摇滚",
                "说唱",
            ),
        )
    )


def _extract_signal(message: str, context_card: Any | None) -> ExtractedSignal | None:
    text = _normalize_message(message)
    if not text:
        return None

    topic = _topic_from_text(text)
    if _has_negative_preference_intent(text) and topic:
        return ExtractedSignal(
            polarity="negative",
            topic=topic,
            statement=f"用户不想在 B 站电台里听 {topic} 相关内容。",
            confidence=0.86,
            kind="stable_preference",
            commit_policy="commit",
        )

    if _has_positive_preference_intent(text) and topic:
        kind = _preference_signal_kind(text)
        commit_policy = "commit" if kind == "stable_preference" else "shadow"
        return ExtractedSignal(
            polarity="positive",
            topic=topic,
            statement=_preference_statement(topic, text, kind),
            confidence=0.88 if kind == "stable_preference" else 0.76,
            kind=kind,
            commit_policy=commit_policy,
        )

    if context_card is not None and len(text) >= 4:
        topic = context_card["topic"] or _topic_from_text(text) or "补充偏好"
        polarity = (
            context_card["polarity"]
            if context_card["polarity"] in {"positive", "negative"}
            else "positive"
        )
        prefix = "用户补充确认" if polarity == "positive" else "用户补充回避"
        return ExtractedSignal(
            polarity=polarity,
            topic=topic,
            statement=f"{prefix}：{context_card['statement'].strip()}；补充原话：{text}",
            confidence=0.78,
            kind="preference_hypothesis",
            commit_policy="shadow",
        )

    return None


def _extract_scene_signal(message: str) -> ExtractedSignal | None:
    text = _normalize_message(message)
    mood = detect_emotion(text)
    scenario = _scene_topic(text, mood)
    if not scenario:
        return None
    return ExtractedSignal(
        polarity="positive",
        topic=scenario["topic"],
        statement=scenario["statement"],
        confidence=scenario["confidence"],
        kind="recent_state",
        commit_policy="shadow",
    )


def _topic_from_text(text: str) -> str:
    artists = matched_artist_names(text)
    if artists:
        return "、".join(artists[:3])
    phrase = topic_phrase(text)
    if phrase:
        return phrase
    for marker in (
        "不喜欢",
        "不爱听",
        "不想听",
        "少推",
        "别推",
        "不要推",
        "避开",
        "喜欢",
        "想听",
        "多推",
        "推荐",
        "推",
    ):
        topic = _extract_topic_after_marker(text, marker)
        if topic:
            return topic
    return ""


def _extract_topic_after_marker(text: str, marker: str) -> str:
    lowered = text.casefold()
    index = lowered.find(marker)
    if index < 0:
        return ""
    fragment = text[index + len(marker):]
    fragment = re.split(r"[，,。.!！?？；;\n\r]", fragment, maxsplit=1)[0]
    return _clean_topic(fragment) or topic_phrase(text)


def _clean_topic(value: str) -> str:
    text = value.strip(" ：:，,。.!！?？；;、")
    text = re.sub(
        r"^(我|你|给我|帮我|以后|以后也|尽量|少|多|听|看|刷|推荐|推|"
        r"一些|一点|几个|几首|那些|这种|这类|类型|风格|歌曲|音乐|内容)+",
        "",
        text,
    )
    text = re.sub(r"(相关|方向|类型|风格|歌曲|音乐|内容).*$", "", text)
    text = text.strip(" ：:，,。.!！?？；;、")
    return text[:80]


def _preference_signal_kind(text: str) -> str:
    if _has_any(text, ("今天", "今晚", "现在", "这会儿", "这轮", "这周", "想听")):
        return "recent_state"
    if _has_any(text, ("最近", "好像", "貌似", "突然", "开始", "有点", "可能", "试试")):
        return "preference_hypothesis"
    if _has_any(text, ("一直", "长期", "忠实粉丝", "从小", "本来就", "很喜欢", "超喜欢")):
        return "stable_preference"
    return "stable_preference"


def _preference_statement(topic: str, text: str, kind: str) -> str:
    if kind == "recent_state":
        return f"用户当前会话想听 {topic} 相关音乐；原话：{text}"
    if kind == "preference_hypothesis":
        return f"用户对 {topic} 产生了偏好假设，需要用少量推荐继续验证；原话：{text}"
    return f"用户明确表示喜欢 {topic} 相关音乐；原话：{text}"


def _scene_topic(text: str, mood: str) -> dict[str, Any] | None:
    if _has_any(text, ("面试", "考试", "复习", "刷题", "写代码", "debug", "工作", "加班")):
        return {
            "topic": "专注放松",
            "statement": f"用户当前处于高认知负荷场景，适合低打扰、稳定、放松的音乐；原话：{text}",
            "confidence": 0.78,
        }
    if _has_any(text, ("夜跑", "跑步", "健身", "运动", "散步")):
        return {
            "topic": "轻律动",
            "statement": f"用户最近有运动场景，适合保留节奏但避免过吵的音乐；原话：{text}",
            "confidence": 0.74,
        }
    if mood:
        topic = {
            "放松": "放松",
            "安静": "安静",
            "开心": "轻快",
            "难过": "温柔",
        }.get(mood, mood)
        return {
            "topic": topic,
            "statement": f"用户当前情绪更接近 {mood}，本轮适合 {topic}、少打扰的音乐；原话：{text}",
            "confidence": 0.72,
        }
    if _has_any(text, ("安静一点", "轻一点", "别太吵", "少干扰", "低打扰")):
        return {
            "topic": "安静",
            "statement": f"用户当前希望听感更安静、少干扰；原话：{text}",
            "confidence": 0.76,
        }
    return None


def _recall_query(message: str) -> str:
    text = _normalize_message(message)
    topics = match_topics(text)
    if topics:
        return " ".join(topics[:2])
    cleaned = re.sub(
        r"(我|你|帮我|给我|之前|以前|上次|貌似|好像|是不是|有没有|听过|找回|记得|的歌|歌曲|音乐)",
        " ",
        text,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:，,。.!！?？；;、")
    return cleaned[:80]


def _recall_reply(query: str, tracks: list[dict[str, Any]]) -> str:
    if tracks:
        subject = query or "最近听过的内容"
        return f"我从听过的记录里找到了这些和 {subject} 接近的歌。"
    if query:
        return f"我没在本地听歌记录里找到 {query}，可以直接让我按这个方向重新推荐。"
    return "我这边还没有足够的本地播放记录。"


def _is_recommendation_explanation_request(text: str) -> bool:
    normalized = text.casefold()
    asks_why = _has_any(normalized, ("为什么", "为啥", "原因", "依据", "怎么会", "怎么给我"))
    about_recommendation = _has_any(normalized, ("推荐", "这几首", "这些歌", "歌单", "给我推"))
    about_item = _explanation_target_index(normalized, 8) is not None
    return (asks_why and about_recommendation) or (asks_why and about_item)


def _explanation_target_index(text: str, total: int) -> int | None:
    if total <= 0:
        return None
    compact = re.sub(r"[\s，,。.!！?？；;、~～]", "", _normalize_message(text).casefold())
    aliases = {
        "第一首": 0,
        "第1首": 0,
        "第一个": 0,
        "第1个": 0,
        "第一条": 0,
        "第1条": 0,
        "第二首": 1,
        "第2首": 1,
        "第二个": 1,
        "第2个": 1,
        "第三首": 2,
        "第3首": 2,
        "第三个": 2,
        "第3个": 2,
    }
    for phrase, index in aliases.items():
        if phrase in compact and index < total:
            return index
    match = re.search(r"第(\d+)(首|个|条)", compact)
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < total:
            return index
    if "这首" in compact or "当前这首" in compact:
        return 0
    return None


def _single_recommendation_explanation(item: dict[str, Any], index: int) -> str:
    title = _compact_text(str(item.get("title") or f"第{index + 1}首"), 48)
    evidence = _clean_trace_list(item.get("evidence"))
    matched = _clean_trace_list(item.get("matchedPreferences"))
    penalties = _clean_trace_list(item.get("penalties"))

    if evidence:
        base = f"第{index + 1}首《{title}》排上来，主要因为{_join_cn(evidence[:2])}。"
    elif matched:
        base = f"第{index + 1}首《{title}》主要命中了 {_join_cn(matched[:3])} 这些方向。"
    else:
        reason = str(item.get("reason") or "").strip()
        if reason:
            base = f"第{index + 1}首《{title}》主要因为{reason}。"
        else:
            base = f"第{index + 1}首《{title}》来自上一轮候选池，当前 trace 里没有更细的单首依据。"

    if penalties:
        return f"{base}它也被 {_join_cn(penalties[:2])} 压过权重，所以不是只靠一个标签硬顶上来。"
    return base


def _trace_matched_preferences(items: list[Any]) -> list[str]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for value in _clean_trace_list(item.get("matchedPreferences")):
            counts[value] = counts.get(value, 0) + 1
    return [
        name
        for name, _count in sorted(counts.items(), key=lambda value: value[1], reverse=True)
    ]


def _trace_penalty_labels(items: list[Any]) -> list[str]:
    labels: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        labels.extend(_clean_trace_list(item.get("penalties")))
    return list(dict.fromkeys(labels))


def _trace_source_labels(items: list[Any]) -> list[str]:
    labels = {
        "explore": "探索",
        "discovery_search": "候选池发现",
        "tag_search": "标签扩展",
        "tag_match": "本地同标签",
        "frequent_up": "常听来源",
        "liked_up": "收藏来源",
        "popular_music": "热门音乐",
        "library": "本地记录",
    }
    result: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = labels.get(str(item.get("source") or ""))
        if label:
            result.append(label)
    return list(dict.fromkeys(result))


def _trace_evidence(items: list[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.extend(_clean_trace_list(item.get("evidence")))
    return list(dict.fromkeys(result))


def _clean_trace_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            result.append(_compact_text(text, 48))
    return result


def _join_cn(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "、".join(items)


def _recommendation_reply(
    *,
    topic: str,
    recommendation_count: int,
    source_text: str,
    profile_hint: str,
) -> str:
    if recommendation_count:
        base = f"我按你刚才说的“{_compact_text(source_text, 42)}”来选，先给你这几首。"
        if profile_hint:
            return f"{base}我也参考了你之前更合拍的 {profile_hint}。"
        return base
    return "这轮没有拿到稳定候选，我先记住当前语境，等你继续听或再说两句后再缩小范围。"


def _control_reply(action: str) -> str:
    return {
        "pause": "好，先暂停。",
        "resume": "好，继续放。",
        "next": "好，切下一首。",
        "previous": "好，回到上一首。",
    }.get(action, "收到。")


def _dialogue_llm_enabled() -> bool:
    raw = os.getenv("RECOMMEND_DIALOGUE_LLM_ENABLED")
    if raw is None:
        raw = os.getenv("RECOMMEND_LLM_ENABLED", "false")
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _dialogue_router_llm_enabled() -> bool:
    raw = os.getenv("RECOMMEND_DIALOGUE_ROUTER_LLM_ENABLED")
    if raw is None:
        return _dialogue_llm_enabled()
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _llm_chat_reply(
    message: str,
    *,
    route: DialogueRoute,
    analysis: dict[str, Any],
    recent_turns: list[dict[str, str]],
) -> str:
    client = _default_llm_client()
    system_prompt = (
        "你是一个自然、敏感、懂音乐的中文 AI 助手，定位像用户的音乐搭子，"
        "但也能正常闲聊和回答日常问题。你必须直接回答用户当前这句话，"
        "不要输出系统状态，不要说 AMEM、画像、记忆写入、工具调用、路由、候选池、trace。"
        "只有用户明确要求推荐可播放歌曲时才说要开始推荐；普通讨论、解释、判断、比较都只聊天。"
        "你可以参考给定的音乐偏好和最近对话，但不要把不确定信息说死。"
        "不要轻易点名单一歌手，除非用户正在问这个歌手；更应该概括风格、情绪和场景。"
        "不要因为画像里某个歌手权重高，就在闲聊里反复提这个歌手。"
        "如果用户问'我是怎么样的人'这类问题，只能基于已知聊天和听歌线索温和推断，"
        "要承认信息有限，并给出有内容的观察。"
        "回复要像真人聊天，2 到 5 句，中文，不要列表，不要 markdown。"
        "返回且只返回 JSON：{\"reply\":\"...\"}"
    )
    user_prompt = json.dumps(
        {
            "user_message": message,
            "route": {
                "tool": route.tool,
                "intent": route.primary_intent,
                "need_profile": route.need_profile,
                "need_memory": route.need_memory,
                "need_recommendation_search": route.need_recommendation_search,
            },
            "music_profile": _compact_profile_for_chat(analysis),
            "recent_dialogue": _compact_recent_turns(recent_turns),
        },
        ensure_ascii=False,
    )
    response = client.complete(system_prompt=system_prompt, user_prompt=user_prompt)
    payload = _parse_json_object(response.content)
    reply = _normalize_message(str(payload.get("reply") or ""))
    if not reply:
        raise ValueError("empty LLM dialogue reply")
    return reply[:1200]


def _compact_profile_for_chat(analysis: dict[str, Any]) -> dict[str, Any]:
    profile = (analysis.get("profile") if isinstance(analysis, dict) else {}) or {}
    summary = (analysis.get("summary") if isinstance(analysis, dict) else {}) or {}
    return {
        "positive_topics": _top_score_items(profile.get("positive_topics"), limit=6),
        "negative_topics": _top_score_items(profile.get("negative_topics"), limit=5),
        "moods": _top_score_items(profile.get("mood_weights"), limit=5),
        "recent_intents": [str(item)[:60] for item in profile.get("recent_intents") or []][:5],
        "source": str(profile.get("source") or ""),
        "confidence": profile.get("confidence"),
        "summary": {
            "top_positive": _summary_names(summary.get("topPositiveTopics"), limit=5),
            "top_negative": _summary_names(summary.get("topNegativeTopics"), limit=4),
            "top_moods": _summary_names(summary.get("topMoods"), limit=4),
        },
    }


def _top_score_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    items = sorted(
        ((str(key), score) for key, score in value.items() if str(key).strip()),
        key=lambda item: float(item[1] or 0),
        reverse=True,
    )
    return [
        {"name": name[:40], "weight": round(float(score or 0), 3)}
        for name, score in items[:limit]
    ]


def _summary_names(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item).strip()
        if name:
            names.append(name[:40])
        if len(names) >= limit:
            break
    return names


def _compact_recent_turns(turns: list[dict[str, str]], *, limit: int = CHAT_LLM_HISTORY_LIMIT) -> list[dict[str, str]]:
    compacted: list[dict[str, str]] = []
    for turn in turns[-limit:]:
        role = str(turn.get("role") or "").strip()
        content = _normalize_message(str(turn.get("content") or ""))
        if role in {"user", "assistant"} and content:
            compacted.append({"role": role, "content": content[:240]})
    return compacted


def _direct_chat_reply(message: str) -> str:
    compact = re.sub(r"[\s，,。.!！?？；;、~～]", "", _normalize_message(message).casefold())
    if compact in {"谢谢", "谢谢你", "谢啦", "thanks", "thankyou"}:
        return "不客气。"
    if compact in {"晚安"}:
        return "晚安，今天就放轻一点。"
    if compact in {"拜拜", "再见", "bye"}:
        return "好，回头继续聊。"
    if compact in {"哈哈", "哈哈哈", "hh", "hhh"}:
        return "哈哈，我在。"
    if compact in {"好", "好的", "ok", "收到"}:
        return "好。"
    return "你好啊，我在。"


def _profile_chat_reply(
    message: str,
    *,
    analysis: dict[str, Any],
    recent_context: list[str],
) -> str:
    normalized = _normalize_message(message)
    artists = matched_artist_names(normalized)
    topics = match_topics(normalized)
    positives = _profile_positive_names(analysis)
    moods = _top_names((analysis.get("profile") or {}).get("mood_weights"))
    style_hint = _profile_style_hint(positives, moods)

    if len(artists) >= 2 and _has_any(normalized, ("哪个", "谁", "更适合", "比较")):
        first, second = artists[0], artists[1]
        return (
            f"这两个里我会先押 {first}，再用 {second} 做扩展。"
            f"{style_hint}如果下一步真要听歌，我会按这个判断去找候选，"
            "但不会让同一个歌手把一整轮占满。"
        )

    if artists and _has_any(normalized, ("类似", "相近", "像谁", "还有没有")):
        similar = _similar_artist_names(artists[0], positives)
        return (
            f"如果从 {artists[0]} 往外扩，我会先看 {similar}。"
            f"{style_hint}这类回答我先只给判断，不直接生成播放列表。"
        )

    if _has_any(normalized, ("是不是", "更喜欢", "我喜欢什么", "我爱听什么")):
        subject = "、".join([*topics, *artists][:2]) or "这个方向"
        if positives:
            return (
                f"有这个趋势，但我不会只凭一句话就把它当成长期结论。"
                f"你现在更明显的底色是 {'、'.join(positives[:3])}，"
                f"{subject} 可以先作为近期探索方向。"
            )
        return (
            f"从这句话看，{subject} 可以先当成一个待验证方向。"
            "我还需要结合后续点击、跳过和你继续聊到的状态再判断。"
        )

    if "歌手" in normalized or _has_any(normalized, ("适合我", "符合我", "品味", "口味")):
        suited = _suited_artist_names(positives, moods, artists)
        extra = ""
        has_work_context = any(
            _has_any(item, ("面试", "写代码", "加班", "复习"))
            for item in recent_context
        )
        if recent_context and has_work_context:
            extra = "你最近如果处在高负荷状态，我会优先选更稳、更少打扰的作品。"
        return (
            f"按你现在的口味，我会先看 {'、'.join(suited)} 这一类。"
            f"{style_hint}{extra}真要进入推荐时，我会控制歌手分布，"
            "不会因为你喜欢某个人就连续塞满。"
        )

    if positives:
        return (
            f"我理解你现在比较吃 {'、'.join(positives[:3])} 这些方向。"
            f"{style_hint}你可以继续聊感受，我会先按最近状态轻量调整，"
            "不把一句话当成永久口味。"
        )
    return (
        "如果只按当前聊天看，我会先从旋律舒服、情绪稳定、不太吵的方向理解你，"
        "再用后续反馈慢慢校准。"
    )


def _profile_update_reply(signal: ExtractedSignal, *, stored: bool) -> str:
    if signal.polarity == "negative":
        return f"收到，后面会减少 {signal.topic} 相关内容，已经点过不感兴趣的也会继续降权。"
    if signal.kind == "recent_state":
        return f"收到，这轮先按 {signal.topic} 来理解，不直接改成永久口味。"
    if signal.kind == "preference_hypothesis":
        return f"收到，{signal.topic} 先作为待验证方向，后面用少量反馈校准。"
    if stored:
        return f"收到，{signal.topic} 会进入后续选歌依据，但我会控制同歌手和同质内容密度。"
    return f"收到，先按 {signal.topic} 这个方向理解。"


def _signal_reply(signal: ExtractedSignal, *, profile_hint: str, stored: bool) -> str:
    if signal.kind == "recent_state":
        suffix = f"你之前更合拍的 {profile_hint} 我也会一起参考。" if profile_hint else ""
        if signal.topic == "专注放松":
            return (
                "面试准备确实容易一直绷着。"
                "这轮推荐会偏低打扰、稳定一点，"
                f"别把情绪再往上推。{suffix}你现在是想边准备边听，还是面完以后放松一下？"
            )
        if signal.topic == "轻律动":
            return (
                "这个状态可以留一点律动，但不需要太炸。"
                f"{suffix}我会先按近期场景处理，不直接改你的长期口味。"
            )
        if signal.topic == "安静":
            return (
                "明白，这轮先把音色和节奏压下来，少一点打扰。"
                f"{suffix}这只是当前状态，不会被当成永久偏好。"
            )
        return (
            f"听起来这轮更适合 {signal.topic}。"
            f"我会先按当前状态处理，不直接改成长期口味。{suffix}"
        )
    if signal.kind == "preference_hypothesis":
        return (
            f"我先把 {signal.topic} 当成一个待验证方向，"
            "后面用少量推荐试探，不会一下子把列表全改掉。"
        )
    if stored:
        return (
            f"收到，{signal.topic} 会成为后续选歌的重要线索。"
            "下一轮我会保留这个方向，但不会让同一个歌手把列表占满。"
        )
    return f"收到，我先按 {signal.topic} 这个方向理解。"


def _casual_reply(message: str, *, profile_hint: str, recent_context: list[str]) -> str:
    del profile_hint
    mood = detect_emotion(message)
    normalized = _normalize_message(message)
    context_hint = ""
    if recent_context and len(recent_context) >= 2:
        context_hint = (
            f"刚才你提到“{_compact_text(recent_context[-1], 28)}”，"
            "我可以顺着这个状态聊。"
        )
    if _is_greeting(message):
        return "在。"
    if _has_any(normalized, ("面试", "写代码", "加班", "复习", "考试", "上班")):
        return "听起来这段时间脑子挺满的。你想先聊两句缓一缓，还是让我给你找点不抢注意力的歌？"
    if mood == "放松":
        return f"那这轮先别太刺激，听感可以轻一点、舒服一点。{context_hint}"
    if mood == "安静":
        return f"我会把节奏压下来，优先考虑安静、低打扰的内容。{context_hint}"
    if mood == "开心":
        return f"可以，那就让节奏稍微亮一点，别太沉。{context_hint}"
    if mood == "难过":
        return f"我明白，先不急着换很吵的歌，可以从更柔和的方向慢慢往外走。{context_hint}"
    return "我在，继续说。"


def _profile_hint(analysis: dict[str, Any]) -> str:
    summary = (analysis.get("summary") if isinstance(analysis, dict) else {}) or {}
    positives = _top_names((analysis.get("profile") or {}).get("positive_topics"))
    if not positives:
        positives = [
            str(item.get("name"))
            for item in summary.get("topPositiveTopics") or []
            if item.get("name")
        ]
    moods = _top_names((analysis.get("profile") or {}).get("mood_weights"))
    names = list(dict.fromkeys([*positives[:2], *moods[:1]]))
    return "、".join(names[:3])


def _profile_positive_names(analysis: dict[str, Any]) -> list[str]:
    summary = (analysis.get("summary") if isinstance(analysis, dict) else {}) or {}
    positives = _top_names((analysis.get("profile") or {}).get("positive_topics"), limit=5)
    if positives:
        return positives
    return [
        str(item.get("name"))
        for item in summary.get("topPositiveTopics") or []
        if isinstance(item, dict) and item.get("name")
    ][:5]


def _profile_style_hint(positives: list[str], moods: list[str]) -> str:
    signals = list(dict.fromkeys([*positives[:3], *moods[:2]]))
    if not signals:
        return "你更像是吃旋律舒服、情绪稳定、不太吵的方向。"
    if any(item in signals for item in ("R&B", "抒情 R&B", "抒情", "治愈系")):
        return "你更像是喜欢旋律舒服、带一点 R&B 底色、不要太炸的听感。"
    if "华语流行" in signals:
        return "你更像是偏旋律清楚、表达直接的华语流行底色。"
    return f"你现在更明显的线索是 {'、'.join(signals[:3])}。"


def _suited_artist_names(
    positives: list[str],
    moods: list[str],
    requested_artists: list[str],
) -> list[str]:
    names: list[str] = []
    profile_text = " ".join([*positives, *moods]).casefold()
    if "r&b" in profile_text or "rnb" in profile_text or "抒情" in profile_text:
        names.extend(["陶喆", "方大同", "王若琳"])
    if "华语" in profile_text or "流行" in profile_text:
        names.extend(["陈奕迅", "孙燕姿", "林俊杰"])
    if "治愈" in profile_text or "温柔" in profile_text:
        names.extend(["毛不易", "田馥甄", "梁静茹"])
    names.extend(requested_artists)
    if not names:
        names.extend(["陶喆", "方大同", "陈奕迅", "孙燕姿"])
    return list(dict.fromkeys(names))[:5]


def _similar_artist_names(seed_artist: str, positives: list[str]) -> str:
    mapping = {
        "陶喆": ["方大同", "王力宏", "胡彦斌", "王若琳"],
        "方大同": ["陶喆", "王若琳", "韦礼安", "王力宏"],
        "周杰伦": ["林俊杰", "陶喆", "王力宏", "孙燕姿"],
        "陈奕迅": ["张学友", "孙燕姿", "田馥甄", "李荣浩"],
        "孙燕姿": ["田馥甄", "梁静茹", "陈奕迅", "王菲"],
    }
    names = mapping.get(seed_artist, [])
    if any(item in {"R&B", "抒情 R&B", "抒情"} for item in positives):
        names = [*names, "陶喆", "方大同", "王若琳"]
    if not names:
        names = ["陶喆", "方大同", "陈奕迅"]
    return "、".join(list(dict.fromkeys(names))[:4])


def _top_names(values: Any, limit: int = 3) -> list[str]:
    if isinstance(values, dict):
        return [
            str(key)
            for key, _value in sorted(
                values.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:limit]
            if str(key).strip()
        ]
    if isinstance(values, list):
        return [
            str(item.get("name"))
            for item in values[:limit]
            if isinstance(item, dict) and item.get("name")
        ]
    return []


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word.casefold() in text.casefold() for word in words)


def _is_greeting(text: str) -> bool:
    normalized = _normalize_message(text).casefold()
    compact = re.sub(r"[\s，,。.!！?？；;、~～]", "", normalized)
    return compact in {
        "你好",
        "你好啊",
        "您好",
        "哈喽",
        "hello",
        "hi",
        "hey",
        "在吗",
        "在不在",
        "嗨",
    }


def _compact_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 1, 1)]}…"


def _escape_like(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _normalize_message(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_action(value: str) -> str:
    action = str(value or "").strip().lower()
    if action not in VALID_ACTIONS:
        raise ValueError("unsupported feedback action")
    return action


def _status_for_action(action: str) -> str:
    if action in CONFIRM_ACTIONS:
        return "confirmed"
    if action in REJECT_ACTIONS:
        return "rejected"
    if action == "later":
        return "deferred"
    return "discussing"


def _state_after_action(action: str) -> str:
    if action in CONFIRM_ACTIONS:
        return "probing"
    if action in REJECT_ACTIONS:
        return "probing"
    if action == "later":
        return "deferred"
    return "discussing"


def _json_loads(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _snapshot_list(snapshot: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = snapshot.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_analysis() -> dict[str, Any]:
    return {
        "scene": "conversation",
        "profile": {
            "positive_topics": {},
            "negative_topics": {},
            "preferred_uploaders": {},
            "avoid_uploaders": {},
            "blocked_uploaders": {},
            "mood_weights": {},
            "recent_intents": [],
            "positive_interest_texts": [],
            "negative_interest_texts": [],
            "same_uploader_limit": 0,
            "exploration_ratio": 0.0,
            "evidence_memory_ids": [],
            "confidence": 0.0,
            "source": "fallback",
        },
        "profileTraceId": "",
        "memories": [],
        "summary": {
            "topPositiveTopics": [],
            "topNegativeTopics": [],
            "topUploaders": [],
            "topMoods": [],
            "strategy": {
                "sameUploaderLimit": 0,
                "explorationRatio": 0.0,
                "confidence": 0.0,
                "source": "fallback",
            },
            "evidenceMemoryCount": 0,
        },
    }
