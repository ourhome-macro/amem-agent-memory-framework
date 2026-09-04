from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from database import DEFAULT_DB_PATH, LEGACY_OWNER_USER_ID, get_connection, init_db
from error_code import APIError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class IdentityService:
    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        *,
        session_ttl_seconds: Optional[int] = None,
        session_idle_seconds: Optional[int] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.session_ttl_seconds = session_ttl_seconds or int(
            os.getenv("APP_SESSION_TTL_SECONDS", str(7 * 24 * 60 * 60))
        )
        self.session_idle_seconds = session_idle_seconds or int(
            os.getenv("APP_SESSION_IDLE_SECONDS", str(24 * 60 * 60))
        )
        self.admin_session_ttl_seconds = int(
            os.getenv("APP_ADMIN_SESSION_TTL_SECONDS", str(8 * 60 * 60))
        )
        self.max_active_sessions = max(int(os.getenv("APP_SESSION_MAX_ACTIVE", "5")), 1)
        metadata_secret = os.getenv("SESSION_METADATA_HMAC_KEY") or os.getenv("APP_SECRET_KEY")
        self.metadata_hmac_key = metadata_secret.encode("utf-8") if metadata_secret else None
        init_db(self.db_path)

    def legacy_user(self) -> dict[str, Any]:
        user = self.get_user(LEGACY_OWNER_USER_ID)
        if user is None:
            raise APIError.auth_required("Legacy owner is not initialized")
        return user

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM app_users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return self._user_dict(row) if row else None

    def authenticate_oidc(
        self,
        *,
        issuer: str,
        subject: str,
        claims: dict[str, Any],
    ) -> dict[str, Any]:
        issuer = issuer.strip().rstrip("/")
        subject = subject.strip()
        if not issuer or not subject:
            raise APIError.auth_required("OIDC response is missing issuer or subject")

        display_name = str(
            claims.get("name")
            or claims.get("preferred_username")
            or claims.get("nickname")
            or subject
        ).strip()[:200]
        email = str(claims.get("email") or "").strip()[:320] or None
        avatar_url = str(claims.get("picture") or "").strip()[:2048]
        groups = claims.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        groups = {str(group) for group in groups if group}

        bootstrap_issuer = os.getenv("OIDC_BOOTSTRAP_ADMIN_ISSUER", "").strip().rstrip("/")
        bootstrap_subject = os.getenv("OIDC_BOOTSTRAP_ADMIN_SUBJECT", "").strip()
        is_bootstrap_admin = bool(
            bootstrap_issuer
            and bootstrap_subject
            and hmac.compare_digest(issuer, bootstrap_issuer)
            and hmac.compare_digest(subject, bootstrap_subject)
        )
        admin_group = os.getenv("OIDC_ADMIN_GROUP", "").strip()
        is_group_admin = bool(admin_group and admin_group in groups)
        now = utc_now()

        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM app_users
                WHERE oidc_issuer = ? AND oidc_subject = ?
                """,
                (issuer, subject),
            ).fetchone()

            if row is None and is_bootstrap_admin:
                owner = conn.execute(
                    "SELECT * FROM app_users WHERE id = ?",
                    (LEGACY_OWNER_USER_ID,),
                ).fetchone()
                if owner and not owner["oidc_issuer"] and not owner["oidc_subject"]:
                    conn.execute(
                        """
                        UPDATE app_users
                        SET oidc_issuer = ?, oidc_subject = ?, display_name = ?,
                            email = ?, avatar_url = ?, role = 'admin',
                            role_source = 'bootstrap', status = 'active',
                            updated_at = ?, last_login_at = ?
                        WHERE id = ?
                        """,
                        (
                            issuer,
                            subject,
                            display_name,
                            email,
                            avatar_url,
                            now,
                            now,
                            LEGACY_OWNER_USER_ID,
                        ),
                    )
                    row = conn.execute(
                        "SELECT * FROM app_users WHERE id = ?",
                        (LEGACY_OWNER_USER_ID,),
                    ).fetchone()
                    self._insert_audit(
                        conn,
                        actor_user_id=LEGACY_OWNER_USER_ID,
                        action="owner.identity_bootstrapped",
                        target_type="app_user",
                        target_id=LEGACY_OWNER_USER_ID,
                        details={"issuer": issuer},
                    )

            if row is None:
                user_id = uuid4().hex
                role = "admin" if is_group_admin else "user"
                role_source = "oidc_group" if is_group_admin else "local"
                conn.execute(
                    """
                    INSERT INTO app_users (
                        id, oidc_issuer, oidc_subject, display_name, email,
                        avatar_url, role, status, role_source, created_at,
                        updated_at, last_login_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        issuer,
                        subject,
                        display_name,
                        email,
                        avatar_url,
                        role,
                        role_source,
                        now,
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM app_users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if role == "admin":
                    self._insert_audit(
                        conn,
                        actor_user_id=user_id,
                        action="identity.admin_created",
                        target_type="app_user",
                        target_id=user_id,
                        details={"source": role_source},
                    )
            else:
                if is_bootstrap_admin and row["id"] != LEGACY_OWNER_USER_ID:
                    raise APIError.conflict(
                        "Bootstrap OIDC identity already belongs to another local user"
                    )
                next_role = row["role"]
                next_role_source = row["role_source"]
                if is_bootstrap_admin:
                    if row["role_source"] != "local":
                        next_role = "admin"
                        next_role_source = "bootstrap"
                elif is_group_admin:
                    next_role = "admin"
                    next_role_source = "oidc_group"
                elif row["role_source"] == "oidc_group":
                    next_role = "user"
                    next_role_source = "local"

                conn.execute(
                    """
                    UPDATE app_users
                    SET display_name = ?, email = ?, avatar_url = ?, role = ?,
                        role_source = ?, updated_at = ?, last_login_at = ?
                    WHERE id = ?
                    """,
                    (
                        display_name,
                        email,
                        avatar_url,
                        next_role,
                        next_role_source,
                        now,
                        now,
                        row["id"],
                    ),
                )
                if next_role != row["role"]:
                    conn.execute(
                        """
                        UPDATE app_sessions
                        SET revoked_at = ?
                        WHERE user_id = ? AND revoked_at IS NULL
                        """,
                        (now, row["id"]),
                    )
                    self._insert_audit(
                        conn,
                        actor_user_id=row["id"],
                        action="identity.role_synchronized",
                        target_type="app_user",
                        target_id=row["id"],
                        details={"role": next_role, "source": next_role_source},
                    )
                row = conn.execute(
                    "SELECT * FROM app_users WHERE id = ?",
                    (row["id"],),
                ).fetchone()

        user = self._user_dict(row)
        if user["status"] != "active":
            raise APIError.auth_required("User account is disabled")
        return user

    def create_session(
        self,
        user_id: str,
        *,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[str, dict[str, Any]]:
        user = self.get_user(user_id)
        if user is None or user["status"] != "active":
            raise APIError.auth_required("User account does not exist or is disabled")
        raw_token = secrets.token_urlsafe(48)
        token_hash = _hash_token(raw_token)
        now_dt = datetime.now(timezone.utc)
        ttl_seconds = self.session_ttl_seconds
        if user["role"] == "admin":
            ttl_seconds = min(ttl_seconds, self.admin_session_ttl_seconds)
        expires_at = now_dt + timedelta(seconds=ttl_seconds)
        ip_hash = self._metadata_fingerprint(ip_address)
        user_agent_hash = self._metadata_fingerprint(user_agent)

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO app_sessions (
                    token_hash, user_id, created_at, expires_at, last_seen_at,
                    revoked_at, ip_hash, user_agent_hash
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    token_hash,
                    user_id,
                    now_dt.isoformat(),
                    expires_at.isoformat(),
                    now_dt.isoformat(),
                    ip_hash,
                    user_agent_hash,
                ),
            )
            conn.execute(
                "DELETE FROM app_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
                (now_dt.isoformat(),),
            )
            stale_sessions = conn.execute(
                """
                SELECT token_hash FROM app_sessions
                WHERE user_id = ? AND revoked_at IS NULL
                ORDER BY created_at DESC
                LIMIT -1 OFFSET ?
                """,
                (user_id, self.max_active_sessions),
            ).fetchall()
            if stale_sessions:
                conn.executemany(
                    "UPDATE app_sessions SET revoked_at = ? WHERE token_hash = ?",
                    [(now_dt.isoformat(), row["token_hash"]) for row in stale_sessions],
                )

        return raw_token, user

    def resolve_session(self, raw_token: Optional[str], *, touch: bool = True) -> Optional[dict[str, Any]]:
        if not raw_token:
            return None
        token_hash = _hash_token(raw_token)
        now_dt = datetime.now(timezone.utc)

        with get_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT s.token_hash, s.expires_at, s.last_seen_at, s.revoked_at,
                       u.id, u.oidc_issuer, u.oidc_subject, u.display_name,
                       u.email, u.avatar_url, u.role, u.status, u.role_source,
                       u.created_at, u.updated_at, u.last_login_at
                FROM app_sessions AS s
                JOIN app_users AS u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked_at"] or row["status"] != "active":
                return None

            expires_at = _parse_timestamp(row["expires_at"])
            last_seen_at = _parse_timestamp(row["last_seen_at"])
            idle_deadline = last_seen_at + timedelta(seconds=self.session_idle_seconds)
            if now_dt >= expires_at or now_dt >= idle_deadline:
                conn.execute(
                    "UPDATE app_sessions SET revoked_at = ? WHERE token_hash = ?",
                    (now_dt.isoformat(), token_hash),
                )
                return None

            if touch and now_dt - last_seen_at >= timedelta(minutes=5):
                conn.execute(
                    "UPDATE app_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now_dt.isoformat(), token_hash),
                )

        return self._user_dict(row)

    def revoke_session(self, raw_token: Optional[str]) -> None:
        if not raw_token:
            return
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE app_sessions SET revoked_at = ? WHERE token_hash = ?",
                (utc_now(), _hash_token(raw_token)),
            )

    def revoke_user_sessions(self, user_id: str) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE app_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (utc_now(), user_id),
            )

    def claim_legacy_owner(self, issuer: str, subject: str) -> dict[str, Any]:
        issuer = issuer.strip().rstrip("/")
        subject = subject.strip()
        if not issuer or not subject:
            raise APIError.validation_error("issuer and subject are required")
        now = utc_now()
        with get_connection(self.db_path) as conn:
            conflict = conn.execute(
                """
                SELECT id FROM app_users
                WHERE oidc_issuer = ? AND oidc_subject = ? AND id <> ?
                """,
                (issuer, subject, LEGACY_OWNER_USER_ID),
            ).fetchone()
            if conflict:
                raise APIError.conflict("OIDC identity already belongs to another user")
            conn.execute(
                """
                UPDATE app_users
                SET oidc_issuer = ?, oidc_subject = ?, role = 'admin',
                    role_source = 'bootstrap', status = 'active', updated_at = ?
                WHERE id = ?
                """,
                (issuer, subject, now, LEGACY_OWNER_USER_ID),
            )
            conn.execute(
                "UPDATE app_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, LEGACY_OWNER_USER_ID),
            )
            self._insert_audit(
                conn,
                actor_user_id=LEGACY_OWNER_USER_ID,
                action="owner.identity_claimed",
                target_type="app_user",
                target_id=LEGACY_OWNER_USER_ID,
                details={"issuer": issuer},
            )
        return self.legacy_user()

    def record_audit(
        self,
        *,
        actor_user_id: str,
        action: str,
        target_type: str,
        target_id: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        with get_connection(self.db_path) as conn:
            self._insert_audit(
                conn,
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                details=details,
            )

    def _metadata_fingerprint(self, value: Optional[str]) -> Optional[str]:
        if not value or not self.metadata_hmac_key:
            return None
        return hmac.new(
            self.metadata_hmac_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _insert_audit(
        conn,
        *,
        actor_user_id: str,
        action: str,
        target_type: str,
        target_id: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO admin_audit_log (
                actor_user_id, action, target_type, target_id,
                request_id, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_user_id,
                action,
                target_type,
                target_id,
                request_id,
                json.dumps(details or {}, ensure_ascii=False),
                utc_now(),
            ),
        )

    @staticmethod
    def _user_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "issuer": row["oidc_issuer"],
            "subject": row["oidc_subject"],
            "displayName": row["display_name"],
            "email": row["email"],
            "avatarUrl": row["avatar_url"],
            "role": row["role"],
            "status": row["status"],
            "roleSource": row["role_source"],
            "createdAt": row["created_at"],
            "lastLoginAt": row["last_login_at"],
        }
