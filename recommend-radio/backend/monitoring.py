from __future__ import annotations

import hmac
import os
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Mapping, Optional
from pathlib import Path

from flask import Flask, Response, g, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)


_HTTP_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    120.0,
)
_UPSTREAM_DURATION_BUCKETS = (
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
_UPSTREAM_OPERATIONS = frozenset(
    {
        "audio_info",
        "auth",
        "auth_qr",
        "comments",
        "cover",
        "favorite",
        "guest_cookie",
        "image",
        "player_info",
        "search",
        "stream",
        "subtitle",
        "video_detail",
    }
)
_UPSTREAM_OUTCOMES = frozenset(
    {"success", "timeout", "rate_limited", "auth_error", "upstream_error"}
)
_DATABASE_OPERATIONS = frozenset(
    {"read", "write", "transaction", "migration", "checkpoint"}
)
_DATABASE_OUTCOMES = frozenset({"success", "busy", "error"})
_ACTIVE_USER_PERIODS = frozenset({"1d", "7d", "30d"})
_AUTH_FLOWS = frozenset({"oidc_login", "oidc_logout", "session_refresh", "bilibili_qr"})
_AUTH_OUTCOMES = frozenset({"success", "denied", "expired", "error"})
_PLAYBACK_ACTIONS = frozenset({"play", "complete", "skip", "favorite", "review"})

UserStatsProvider = Callable[[], tuple[int, Mapping[str, int]]]


HTTP_REQUESTS = Counter(
    "bilibili_radio_http_requests_total",
    "HTTP responses produced by the Flask application.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "bilibili_radio_http_request_duration_seconds",
    "Time from Flask request entry until response headers are produced.",
    ("method", "route"),
    buckets=_HTTP_DURATION_BUCKETS,
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "bilibili_radio_http_requests_in_progress",
    "HTTP requests currently executing in Flask workers.",
    ("method", "route"),
    multiprocess_mode="livesum",
)
HTTP_REQUEST_BYTES = Counter(
    "bilibili_radio_http_request_bytes_total",
    "HTTP request body bytes reported by Content-Length.",
    ("method", "route"),
)
HTTP_RESPONSE_BYTES = Counter(
    "bilibili_radio_http_response_bytes_total",
    "HTTP response bytes reported by Content-Length.",
    ("method", "route"),
)

BILIBILI_UPSTREAM_REQUESTS = Counter(
    "bilibili_radio_bilibili_upstream_requests_total",
    "Calls made to Bilibili upstream services.",
    ("operation", "outcome"),
)
BILIBILI_UPSTREAM_DURATION = Histogram(
    "bilibili_radio_bilibili_upstream_request_duration_seconds",
    "Duration of Bilibili upstream calls.",
    ("operation",),
    buckets=_UPSTREAM_DURATION_BUCKETS,
)

DATABASE_OPERATIONS = Counter(
    "bilibili_radio_database_operations_total",
    "SQLite operations by coarse operation and outcome.",
    ("operation", "outcome"),
)
DATABASE_OPERATION_DURATION = Histogram(
    "bilibili_radio_database_operation_duration_seconds",
    "Duration of SQLite operations.",
    ("operation",),
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

ACTIVE_AUDIO_STREAMS = Gauge(
    "bilibili_radio_active_audio_streams",
    "Audio streams currently open across all Gunicorn workers.",
    multiprocess_mode="livesum",
)
AUDIO_STREAMS = Counter(
    "bilibili_radio_audio_streams_total",
    "Audio streams opened by final outcome.",
    ("outcome",),
)
AUDIO_STREAM_BYTES = Counter(
    "bilibili_radio_audio_stream_bytes_total",
    "Audio payload bytes sent to clients.",
)

REGISTERED_USERS = Gauge(
    "bilibili_radio_registered_users",
    "Registered local application users.",
    multiprocess_mode="livemostrecent",
)
ACTIVE_USERS = Gauge(
    "bilibili_radio_active_users",
    "Users active in a bounded reporting period.",
    ("period",),
    multiprocess_mode="livemostrecent",
)
AUTH_EVENTS = Counter(
    "bilibili_radio_auth_events_total",
    "Authentication events without user-identifying labels.",
    ("flow", "outcome"),
)
PLAYBACK_EVENTS = Counter(
    "bilibili_radio_playback_events_total",
    "Coarse playback and library actions without track or user labels.",
    ("action",),
)
METRIC_REFRESH_FAILURES = Counter(
    "bilibili_radio_metric_refresh_failures_total",
    "Failures while refreshing database-derived metrics.",
    ("provider",),
)


def register_monitoring(
    app: Flask,
    user_stats_provider: Optional[UserStatsProvider] = None,
) -> None:
    """Register low-cardinality HTTP metrics and the internal scrape endpoint."""
    if app.extensions.get("bilibili_radio_monitoring"):
        return
    app.extensions["bilibili_radio_monitoring"] = True

    @app.before_request
    def _monitor_request_start() -> None:
        route = _route_label()
        if route in {"/internal/metrics", "/health/live", "/health/ready"}:
            return

        method = _method_label()
        g.monitoring_request = {
            "started_at": time.perf_counter(),
            "method": method,
            "route": route,
            "active": True,
        }
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method, route=route).inc()

        content_length = request.content_length
        if content_length is not None and content_length > 0:
            HTTP_REQUEST_BYTES.labels(method=method, route=route).inc(content_length)

    @app.after_request
    def _monitor_request_end(response: Response) -> Response:
        state = getattr(g, "monitoring_request", None)
        if not state or not state["active"]:
            return response

        method = state["method"]
        route = state["route"]
        duration = max(0.0, time.perf_counter() - state["started_at"])
        HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration)
        HTTP_REQUESTS.labels(
            method=method,
            route=route,
            status=str(response.status_code),
        ).inc()

        content_length = response.content_length
        if content_length is not None and content_length > 0:
            HTTP_RESPONSE_BYTES.labels(method=method, route=route).inc(content_length)

        _finish_in_progress(state)
        return response

    @app.teardown_request
    def _monitor_request_teardown(_error: Optional[BaseException]) -> None:
        state = getattr(g, "monitoring_request", None)
        if state:
            _finish_in_progress(state)

    app.add_url_rule(
        "/internal/metrics",
        endpoint="internal_prometheus_metrics",
        view_func=lambda: _metrics_response(app, user_stats_provider),
        methods=["GET"],
    )


def record_bilibili_request(operation: str, outcome: str, duration_seconds: float) -> None:
    operation = _bounded(operation, _UPSTREAM_OPERATIONS)
    outcome = _bounded(outcome, _UPSTREAM_OUTCOMES)
    BILIBILI_UPSTREAM_REQUESTS.labels(operation=operation, outcome=outcome).inc()
    BILIBILI_UPSTREAM_DURATION.labels(operation=operation).observe(max(0.0, duration_seconds))


@contextmanager
def observe_bilibili_request(operation: str) -> Iterator[None]:
    """Record a call as success/error; use the explicit function for richer outcomes."""
    started_at = time.perf_counter()
    outcome = "success"
    try:
        yield
    except BaseException:
        outcome = "upstream_error"
        raise
    finally:
        record_bilibili_request(operation, outcome, time.perf_counter() - started_at)


def record_database_operation(operation: str, outcome: str, duration_seconds: float) -> None:
    operation = _bounded(operation, _DATABASE_OPERATIONS)
    outcome = _bounded(outcome, _DATABASE_OUTCOMES)
    DATABASE_OPERATIONS.labels(operation=operation, outcome=outcome).inc()
    DATABASE_OPERATION_DURATION.labels(operation=operation).observe(max(0.0, duration_seconds))


def audio_stream_opened() -> None:
    ACTIVE_AUDIO_STREAMS.inc()


def audio_stream_closed(bytes_sent: int, outcome: str = "completed") -> None:
    ACTIVE_AUDIO_STREAMS.dec()
    AUDIO_STREAMS.labels(outcome=_stream_outcome(outcome)).inc()
    if bytes_sent > 0:
        AUDIO_STREAM_BYTES.inc(bytes_sent)


def set_user_totals(total: int, active_by_period: Optional[dict[str, int]] = None) -> None:
    REGISTERED_USERS.set(max(0, total))
    for period, active in (active_by_period or {}).items():
        if period in _ACTIVE_USER_PERIODS:
            ACTIVE_USERS.labels(period=period).set(max(0, active))


def record_auth_event(flow: str, outcome: str) -> None:
    AUTH_EVENTS.labels(
        flow=_bounded(flow, _AUTH_FLOWS),
        outcome=_bounded(outcome, _AUTH_OUTCOMES),
    ).inc()


def record_playback_event(action: str) -> None:
    PLAYBACK_EVENTS.labels(action=_bounded(action, _PLAYBACK_ACTIONS)).inc()


def _metrics_response(
    app: Flask,
    user_stats_provider: Optional[UserStatsProvider],
) -> Response:
    if not _env_flag("ENABLE_METRICS", default=False):
        return Response(status=404)
    expected_token = _metrics_bearer_token()
    supplied_header = request.headers.get("Authorization", "")
    supplied_token = supplied_header[7:] if supplied_header.startswith("Bearer ") else ""
    if not expected_token:
        app.logger.error("ENABLE_METRICS requires a metrics bearer token")
        return Response(status=503)
    if not supplied_token or not hmac.compare_digest(expected_token, supplied_token):
        return Response(status=401, headers={"WWW-Authenticate": "Bearer"})

    if user_stats_provider is not None:
        try:
            total, active_by_period = user_stats_provider()
            set_user_totals(total, dict(active_by_period))
        except Exception:
            METRIC_REFRESH_FAILURES.labels(provider="user_stats").inc()
            app.logger.exception("Failed to refresh Prometheus user metrics")

    registry = _scrape_registry()
    return Response(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)


def _scrape_registry():
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


def _finish_in_progress(state: dict[str, object]) -> None:
    if not state["active"]:
        return
    state["active"] = False
    HTTP_REQUESTS_IN_PROGRESS.labels(
        method=str(state["method"]),
        route=str(state["route"]),
    ).dec()


def _route_label() -> str:
    rule = request.url_rule
    return rule.rule if rule is not None else "unmatched"


def _method_label() -> str:
    method = request.method.upper()
    return method if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else "OTHER"


def _bounded(value: str, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else "other"


def _stream_outcome(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"completed", "client_closed", "upstream_error"} else "other"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _metrics_bearer_token() -> str:
    token_file = os.environ.get("METRICS_BEARER_TOKEN_FILE", "").strip()
    if token_file:
        try:
            return Path(token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return os.environ.get("METRICS_BEARER_TOKEN", "").strip()
