"""Recover missing legacy session parents without deleting history or guessing ownership."""

import sqlite3


def repair_session_references(path):
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "agent_dialogue_sessions" not in tables:
            return
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        sources = (
            "agent_dialogue_turns",
            "agent_dialogue_cards",
            "agent_dialogue_checkpoints",
            "conversation_warm_topics",
            "agent_dialogue_signals",
        )
        missing = set()
        for table in sources:
            if table in tables:
                missing.update(
                    row[0]
                    for row in conn.execute(
                        f"SELECT DISTINCT session_id FROM {table} "
                        "WHERE session_id NOT IN (SELECT session_id FROM agent_dialogue_sessions)"
                    )
                )
        repairs = []
        for session_id in missing:
            owners = set()
            timestamps = []
            for table in (
                "agent_dialogue_checkpoints",
                "conversation_warm_topics",
                "agent_dialogue_signals",
            ):
                if table not in tables:
                    continue
                column = "updated_at" if table == "conversation_warm_topics" else "created_at"
                for owner, stamp in conn.execute(
                    f"SELECT user_id,{column} FROM {table} WHERE session_id=?", (session_id,)
                ):
                    owners.add(owner)
                    timestamps.append(stamp)
            if len(owners) != 1 or not timestamps:
                raise RuntimeError(
                    "Legacy session ownership is ambiguous; migration requires review"
                )
            owner = next(iter(owners))
            if not conn.execute("SELECT 1 FROM app_users WHERE id=?", (owner,)).fetchone():
                raise RuntimeError(
                    "Legacy session owner no longer exists; migration requires review"
                )
            repairs.append((session_id, owner, min(timestamps), max(timestamps)))
        if not repairs:
            return
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS migration_repairs "
            "(session_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,reason TEXT NOT NULL)"
        )
        for session_id, owner, created, updated in repairs:
            conn.execute(
                "INSERT INTO agent_dialogue_sessions "
                "(session_id,user_id,state,focus,created_at,updated_at,pending_context_json) "
                "VALUES (?,?,'archived','migration_recovered',?,?,'{}')",
                (session_id, owner, created, updated),
            )
            conn.execute(
                "INSERT INTO migration_repairs VALUES (?,?,?)",
                (session_id, owner, "Recovered missing parent from historical ownership"),
            )
        remaining = conn.execute("PRAGMA foreign_key_check").fetchall()
        if remaining:
            raise RuntimeError(
                f"Legacy foreign key repair incomplete ({len(remaining)} of {len(violations)})"
            )
