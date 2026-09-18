"""Shared OpenTelemetry setup and W3C propagation without business payload capture."""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import wraps
from threading import Lock

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_lock = Lock()
_pid = None


def configure(service: str):
    global _pid
    with _lock:
        if _pid == os.getpid():
            return trace.get_tracer_provider()
        provider = TracerProvider(
            resource=Resource.create({"service.name": service, "service.version": "0.6.0"})
        )
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=5),
                    schedule_delay_millis=500,
                )
            )
        trace.set_tracer_provider(provider)
        _pid = os.getpid()
        return provider


def carrier() -> dict[str, str]:
    values = {}
    propagate.inject(values)
    return {key: value for key, value in values.items() if key in {"traceparent", "tracestate"}}


@contextmanager
def span(name: str, *, parent: dict | None = None, attributes: dict | None = None):
    kwargs = {"context": propagate.extract(parent)} if parent else {}
    # Do not put exception messages, prompts, cookies or credentials in spans.
    with trace.get_tracer("amem.runtime").start_as_current_span(
        name,
        attributes=attributes or {},
        record_exception=False,
        set_status_on_exception=False,
        **kwargs,
    ) as current:
        try:
            yield current
        except BaseException as error:
            current.set_attribute("error.type", type(error).__name__)
            current.set_status(trace.Status(trace.StatusCode.ERROR))
            raise


def trace_id() -> str:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else ""


def flush() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)


def traced(name: str):
    def decorate(function):
        @wraps(function)
        def execute(self, *args, **kwargs):
            with span(name, attributes={"gen_ai.request.model": str(getattr(self, "model", ""))}):
                return function(self, *args, **kwargs)

        return execute

    return decorate
