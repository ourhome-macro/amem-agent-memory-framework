from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import get_connection
from error_code import APIError
from profile_projector import _default_llm_client
from settings_service import DEEPSEEK_API_KEY_SETTING, SettingsService


def _add_user(db_path, user_id: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO app_users (id, display_name, created_at, updated_at)
               VALUES (?, ?, '2026-01-01', '2026-01-01')""",
            (user_id, user_id),
        )


def test_deepseek_key_is_encrypted_scoped_and_overrides_environment(tmp_path, monkeypatch):
    db_path = tmp_path / "radio.sqlite3"
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-" + "x" * 48)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    alice = SettingsService(db_path=db_path, user_id="alice")
    bob = SettingsService(db_path=db_path, user_id="bob")
    _add_user(db_path, "alice")
    _add_user(db_path, "bob")

    alice.set_deepseek_api_key("alice-key")
    with get_connection(db_path) as conn:
        stored = conn.execute(
            "SELECT value FROM settings WHERE user_id = ? AND key = ?",
            ("alice", DEEPSEEK_API_KEY_SETTING),
        ).fetchone()["value"]
    assert "alice-key" not in stored
    assert alice.get_deepseek_api_key() == "alice-key"
    assert bob.get_deepseek_api_key() is None
    assert _default_llm_client(user_id="alice", db_path=str(db_path))._api_key() == "alice-key"
    with pytest.raises(RuntimeError, match="Personal DeepSeek"):
        _default_llm_client(user_id="bob", db_path=str(db_path))._api_key()
    assert os.environ["DEEPSEEK_API_KEY"] == "environment-key"

    from agent_memory_runtime.llm import transport

    credentials = []

    def fake_openai_client(**kwargs):
        credentials.append(kwargs["api_key"])
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_kwargs: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
                    )
                )
            )
        )

    monkeypatch.setattr(transport, "get_openai_client", fake_openai_client)
    _default_llm_client(user_id="alice", db_path=str(db_path)).complete(
        system_prompt="test", user_prompt="test"
    )
    assert credentials == ["alice-key"]

    alice.set_deepseek_api_key("rotated-key")
    assert _default_llm_client(user_id="alice", db_path=str(db_path))._api_key() == "rotated-key"
    alice.delete_deepseek_api_key()
    with pytest.raises(RuntimeError, match="Personal DeepSeek"):
        _default_llm_client(user_id="alice", db_path=str(db_path))._api_key()


def test_deepseek_key_requires_stable_app_secret_and_rejects_bad_input(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    service = SettingsService(db_path=tmp_path / "radio.sqlite3", user_id="alice")
    _add_user(service.db_path, "alice")
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        service.set_deepseek_api_key("valid-key")
    with pytest.raises(APIError):
        service.set_deepseek_api_key("key with spaces")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-" + "x" * 48)
    service.set_deepseek_api_key("valid-key")
    monkeypatch.setenv("APP_SECRET_KEY", "different-secret-" + "x" * 48)
    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        service.get_deepseek_api_key()


def test_profile_projection_uses_rules_without_personal_key(tmp_path, monkeypatch):
    import profile_projector
    import settings_service
    from music_profile import MusicProfile

    monkeypatch.setattr(settings_service, "DEFAULT_DB_PATH", tmp_path / "radio.sqlite3")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-key")

    def unexpected_llm(**_kwargs):
        raise AssertionError("server-wide key must not be used for a user without a personal key")

    monkeypatch.setattr(profile_projector, "_default_llm_client", unexpected_llm)
    retriever = SimpleNamespace(retrieve_memories=lambda *_args, **_kwargs: [])
    projector = profile_projector.ProfileProjector(retriever, enabled=True)
    result = projector.project(
        user_id="legacy-owner", scene="home", fallback_profile=MusicProfile.empty()
    )
    assert result.profile.source == "fallback"


def test_recommendations_skip_llm_profile_without_personal_key(tmp_path, monkeypatch):
    from recommendation_service import RecommendationService

    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-key")

    class UnexpectedProjector:
        def project(self, **_kwargs):
            raise AssertionError("recommendations must use the rule profile without a personal key")

    service = RecommendationService(
        db_path=tmp_path / "radio.sqlite3",
        user_id="legacy-owner",
        profile_projector=UnexpectedProjector(),
        auto_discovery=False,
    )
    result = service.list_recommendations(scene="home", limit=1)
    assert result["items"] == []


def test_settings_api_never_returns_secret(tmp_path):
    env = dict(os.environ)
    env.update(
        APP_DATA_DIR=str(tmp_path),
        APP_SECRET_KEY="test-secret-" + "x" * 48,
        DEEPSEEK_API_KEY="server-key",
        RECOMMEND_LLM_PROVIDER="deepseek",
        AUTH_MODE="disabled",
        AMEM_ENABLED="false",
        AMEM_TRANSPORT="embedded",
        RABBITMQ_ENABLED="false",
        RECOMMEND_LLM_ENABLED="false",
        REQUIRE_DB_MIGRATION="0",
    )
    script = """
from app import app
from database import get_connection
from durable_jobs import enqueue
client = app.test_client()
path = '/api/settings/deepseek-key'
before = client.get(path)
assert before.status_code == 200
assert before.json['data'] == {'configured': False, 'fallbackConfigured': True, 'provider': 'deepseek'}
assert client.post('/api/agent/dialogue/sessions').status_code == 403
saved = client.put(path, json={'deepseekApiKey': 'personal-key'})
assert saved.status_code == 200 and saved.json['data'] == {'configured': True}
assert saved.headers['Cache-Control'] == 'no-store'
after = client.get(path)
assert after.json['data'] == {'configured': True, 'fallbackConfigured': True, 'provider': 'deepseek'}
assert 'personal-key' not in after.get_data(as_text=True)
assert 'server-key' not in after.get_data(as_text=True)
created = client.post('/api/agent/dialogue/sessions')
assert created.status_code == 201
session_id = created.json['data']['sessionId']
deleted = client.delete(path)
assert deleted.status_code == 200 and deleted.json['data'] == {'configured': False}
assert client.get(path).json['data']['configured'] is False
assert client.post('/api/agent/dialogue/sessions').status_code == 403
with get_connection() as conn:
    enqueue(conn, kind='dialogue', user_id='legacy-owner',
            payload={'session_id': session_id, 'message': 'pending'})
assert client.delete('/api/agent/dialogue/sessions/' + session_id).status_code == 409
with get_connection() as conn:
    conn.execute("UPDATE durable_jobs SET status='completed' WHERE kind='dialogue' AND user_id='legacy-owner'")
assert client.delete('/api/agent/dialogue/sessions/' + session_id).status_code == 200
with get_connection() as conn:
    assert conn.execute('SELECT 1 FROM agent_dialogue_turns WHERE session_id=?', (session_id,)).fetchone() is None
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_api_key_and_dialogue_are_scoped_to_the_logged_in_user(tmp_path):
    env = dict(os.environ)
    env.update(
        APP_DATA_DIR=str(tmp_path),
        APP_SECRET_KEY="test-secret-" + "x" * 48,
        AUTH_MODE="disabled",
        AMEM_ENABLED="false",
        AMEM_TRANSPORT="embedded",
        RABBITMQ_ENABLED="false",
        RECOMMEND_LLM_ENABLED="false",
        REQUIRE_DB_MIGRATION="0",
    )
    script = """
from flask import request
from app import app, oidc_auth
from database import get_connection
with get_connection() as conn:
    for user_id in ('alice', 'bob'):
        conn.execute(
            "INSERT INTO app_users (id, display_name, created_at, updated_at) VALUES (?, ?, 'now', 'now')",
            (user_id, user_id),
        )
oidc_auth.current_user = lambda _token: {'id': request.headers['X-Test-User']}
client = app.test_client()
alice = {'X-Test-User': 'alice'}
bob = {'X-Test-User': 'bob'}
saved = client.put('/api/settings/deepseek-key', json={'deepseekApiKey': 'alice-private'}, headers=alice)
assert saved.status_code == 200, saved.data
assert client.get('/api/settings/deepseek-key', headers=alice).json['data']['configured'] is True
assert client.get('/api/settings/deepseek-key', headers=bob).json['data']['configured'] is False
assert client.post('/api/agent/dialogue/sessions', headers=bob).status_code == 403
session = client.post('/api/agent/dialogue/sessions', headers=alice).json['data']
alice_session_id = session['sessionId']
assert client.delete('/api/agent/dialogue/sessions/' + alice_session_id, headers=bob).status_code == 404
assert any(item['sessionId'] == alice_session_id for item in
           client.get('/api/agent/dialogue/sessions', headers=alice).json['data']['items'])
assert all(item['sessionId'] != alice_session_id for item in
           client.get('/api/agent/dialogue/sessions', headers=bob).json['data']['items'])
foreign = client.get('/api/agent/dialogue', query_string={'sessionId': alice_session_id}, headers=bob)
assert foreign.status_code == 200
assert foreign.json['data']['sessionId'] != alice_session_id
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
