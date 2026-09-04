from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from authlib.integrations.flask_client import OAuth
from flask import Flask, Response, redirect, request, session, url_for

from error_code import APIError
from identity_service import IdentityService


class OIDCAuth:
    def __init__(self, app: Flask, identity_service: IdentityService):
        self.app = app
        self.identity_service = identity_service
        self.mode = os.getenv("AUTH_MODE", "disabled").strip().lower()
        if self.mode not in {"disabled", "oidc"}:
            raise RuntimeError("AUTH_MODE must be either 'disabled' or 'oidc'")

        self.issuer = os.getenv("OIDC_ISSUER_URL", "").strip().rstrip("/")
        self.client_id = os.getenv("OIDC_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("OIDC_CLIENT_SECRET", "").strip()
        self.external_url = os.getenv("APP_EXTERNAL_URL", "").strip().rstrip("/")
        self.cookie_secure = _env_bool("SESSION_COOKIE_SECURE", self.mode == "oidc")
        self.cookie_name = os.getenv(
            "APP_SESSION_COOKIE_NAME",
            "__Host-br_session" if self.cookie_secure else "br_session",
        ).strip()
        self.cookie_max_age = identity_service.session_ttl_seconds
        self.csrf_secret = app.secret_key.encode("utf-8")
        self.oauth: Optional[OAuth] = None
        self.client: Any = None

        if self.mode == "oidc":
            missing = [
                name
                for name, value in (
                    ("APP_SECRET_KEY", os.getenv("APP_SECRET_KEY", "")),
                    ("OIDC_ISSUER_URL", self.issuer),
                    ("OIDC_CLIENT_ID", self.client_id),
                    ("OIDC_CLIENT_SECRET", self.client_secret),
                    ("APP_EXTERNAL_URL", self.external_url),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(f"OIDC mode requires: {', '.join(missing)}")
            if not self.issuer.startswith("https://") and not _env_bool("OIDC_ALLOW_HTTP", False):
                raise RuntimeError("OIDC_ISSUER_URL must use HTTPS")
            self._validate_external_url()

            self.oauth = OAuth(app)
            self.client = self.oauth.register(
                name="oidc",
                client_id=self.client_id,
                client_secret=self.client_secret,
                server_metadata_url=f"{self.issuer}/.well-known/openid-configuration",
                client_kwargs={
                    "scope": os.getenv("OIDC_SCOPES", "openid profile email"),
                    "code_challenge_method": "S256",
                },
            )

    @property
    def enabled(self) -> bool:
        return self.mode == "oidc"

    def current_user(self, raw_token: Optional[str], *, touch: bool = True) -> Optional[dict[str, Any]]:
        if not self.enabled:
            return self.identity_service.legacy_user()
        return self.identity_service.resolve_session(raw_token, touch=touch)

    def begin_login(self, next_url: Optional[str] = None) -> Response:
        if not self.enabled:
            return redirect(self._safe_next_url(next_url))
        target = self._safe_next_url(next_url)
        session.clear()
        session["oidc_next"] = target
        return self.client.authorize_redirect(
            self._callback_url(),
            code_challenge_method="S256",
        )

    def finish_login(self) -> tuple[Response, str, dict[str, Any]]:
        if not self.enabled:
            raise APIError.validation_error("OIDC authentication is disabled")
        target = self._safe_next_url(session.get("oidc_next"))
        token = self.client.authorize_access_token()
        claims = token.get("userinfo")
        if not isinstance(claims, dict):
            claims = self.client.parse_id_token(token)
        if not isinstance(claims, dict):
            raise APIError.auth_required("OIDC provider returned no identity claims")

        token_issuer = str(claims.get("iss") or self.issuer).rstrip("/")
        if not hmac.compare_digest(token_issuer, self.issuer):
            raise APIError.auth_required("OIDC issuer mismatch")
        subject = str(claims.get("sub") or "").strip()
        user = self.identity_service.authenticate_oidc(
            issuer=self.issuer,
            subject=subject,
            claims=claims,
        )
        self.identity_service.revoke_session(request.cookies.get(self.cookie_name))
        raw_session, user = self.identity_service.create_session(
            user["id"],
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
        session.clear()
        response = redirect(target)
        self.set_session_cookie(response, raw_session)
        return response, raw_session, user

    def logout(self, response: Response, raw_token: Optional[str]) -> None:
        self.identity_service.revoke_session(raw_token)
        session.clear()
        response.delete_cookie(
            self.cookie_name,
            path="/",
            secure=self.cookie_secure,
            httponly=True,
            samesite="Lax",
        )

    def set_session_cookie(self, response: Response, raw_token: str) -> None:
        response.set_cookie(
            self.cookie_name,
            raw_token,
            max_age=self.cookie_max_age,
            secure=self.cookie_secure,
            httponly=True,
            samesite="Lax",
            path="/",
        )

    def csrf_token(self, raw_session: Optional[str]) -> Optional[str]:
        if not self.enabled:
            return None
        if not raw_session:
            return None
        return hmac.new(
            self.csrf_secret,
            f"csrf:{raw_session}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def validate_csrf(self, raw_session: Optional[str], supplied_token: Optional[str]) -> bool:
        expected = self.csrf_token(raw_session)
        return bool(expected and supplied_token and hmac.compare_digest(expected, supplied_token))

    def _callback_url(self) -> str:
        callback_path = url_for("session_callback")
        return f"{self.external_url}{callback_path}"

    def _validate_external_url(self) -> None:
        parsed = urlparse(self.external_url)
        allow_http = _env_bool("OIDC_ALLOW_HTTP", False)
        if (
            parsed.scheme not in ({"https", "http"} if allow_http else {"https"})
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or (parsed.path and parsed.path != "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("APP_EXTERNAL_URL must be an HTTPS origin without a path")

    @staticmethod
    def _safe_next_url(value: Optional[str]) -> str:
        candidate = str(value or "/").strip()
        decoded = unquote(candidate)
        parsed = urlparse(candidate)
        if (
            not candidate.startswith("/")
            or candidate.startswith("//")
            or "\\" in decoded
            or any(ord(character) < 0x20 for character in decoded)
        ):
            return "/"
        if parsed.scheme or parsed.netloc:
            return "/"
        return candidate


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
