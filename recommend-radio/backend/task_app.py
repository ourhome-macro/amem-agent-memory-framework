from __future__ import annotations

import json
import logging
from urllib.parse import quote

from celery import Celery
from database import DEFAULT_DB_PATH, get_connection, init_db
from durable_jobs import claim, finish, publish_pending
from kombu import Exchange, Queue
from rabbitmq_bus import RabbitMQSettings

LOGGER = logging.getLogger("recommend-radio.tasks")
settings = RabbitMQSettings.from_env()
settings.validate()
broker = (
    f"amqp://{quote(settings.user, safe='')}:{quote(settings.password, safe='')}@"
    f"{settings.host}:{settings.port}/{quote(settings.virtual_host, safe='')}"
)
app = Celery("recommend-radio", broker=broker)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_soft_time_limit=240,
    task_time_limit=270,
    broker_heartbeat=30,
    broker_connection_timeout=5,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"confirm_publish": True},
    # RabbitMQ 4.3 rejects transient non-exclusive queues by default. Control
    # replies and event listeners are connection-scoped, so make them exclusive.
    control_queue_exclusive=True,
    event_queue_exclusive=True,
    task_publish_retry=False,
    task_create_missing_queues=False,
    task_default_queue="radio.jobs.v2",
    task_default_queue_type="quorum",
    task_queues=tuple(
        Queue(
            name,
            exchange=Exchange(name, type="direct", durable=True),
            routing_key=name,
            durable=True,
            queue_arguments={"x-queue-type": "quorum"},
        )
        for name in ("radio.jobs.v2", "radio.events.v2")
    ),
)


def publish_job(job_id: str, kind: str) -> None:
    from telemetry_setup import setup
    from agent_memory_runtime.telemetry import carrier, span
    setup('radio-outbox')
    queue = "radio.events.v2" if kind in {"behavior", "sse"} else "radio.jobs.v2"
    with get_connection() as conn:
        row = conn.execute('SELECT trace_context FROM durable_jobs WHERE job_id=?', (job_id,)).fetchone()
    parent = json.loads(row['trace_context']) if row else {}
    with span('messaging.publish', parent=parent, attributes={'job.kind':kind,'job.id':job_id}):
        execute_job.apply_async(args=[job_id], task_id=job_id, queue=queue,
                               routing_key=queue, delivery_mode=2, headers=carrier())


@app.task(bind=True, name="radio.execute_job")
def execute_job(self, job_id: str) -> None:
    from telemetry_setup import setup
    from agent_memory_runtime.telemetry import span, flush
    from music_agent import current_job_id
    setup('radio-worker')
    init_db()
    with get_connection() as conn:
        job = claim(conn, job_id)
    if job is None:
        return
    parent = self.request.headers or json.loads(job['trace_context'])
    token = current_job_id.set(job_id)
    try:
        with span('task.execute', parent=parent, attributes={'job.id':job_id,'job.kind':job['kind']}):
            try:
                result = dispatch(job)
            except Exception as error:
                LOGGER.exception("Job failed: %s", job_id)
                with get_connection() as conn:
                    finish(conn, job, error=error)
                raise
            else:
                with get_connection() as conn:
                    finish(conn, job, result=result)
                if job["kind"] == "sse":
                    try:
                        publish_pending(DEFAULT_DB_PATH, publish_job)
                    except Exception:
                        LOGGER.exception("Could not immediately publish the next SSE event")
    finally:
        current_job_id.reset(token)
        flush()


def dispatch(job: dict):
    payload = json.loads(job["payload_json"])
    if job["kind"] == "behavior":
        from amem_grpc_bridge import AmemGrpcBridge

        bridge = AmemGrpcBridge.from_env()
        try:
            return bridge.record_behavior(payload)
        finally:
            bridge.channel.close()
    if job["kind"] == "sse":
        from sse_event_client import SSEEventPublisher

        publisher = SSEEventPublisher()
        try:
            return publisher.publish_http(**payload)
        finally:
            publisher.close()

    # Never deserialize request cookies, Python service instances or caller-owned
    # paths from broker payloads. Identity and input come from the durable row.
    from service_factory import MusicServices

    user_id = job["user_id"]
    with MusicServices(user_id=user_id) as services:
        if job["kind"] in {"discovery", "evolution"}:
            from request_spec import RequestSpec

            service = services.discovery
            if job["kind"] == "evolution":
                if service.keyword_governance.evolution_due():
                    service._run_evolution(payload["blocked_topics"])
                return None
            service._run_job(
                job["job_id"],
                payload["queries"],
                payload["negative_queries"],
                payload["keyword_specs"],
                payload["negative_keyword_specs"],
                RequestSpec.from_dict(payload["request_spec"]),
                payload["limit"],
            )
            status = service.job_status(job["job_id"])
            if status["status"] == "failed":
                raise RuntimeError("DiscoveryFailed")
            return status
        if job["kind"] in {"dialogue", "discovery_watch"}:
            from dialogue_task_service import DialogueTaskService
            from sse_event_client import SSEEventPublisher

            service = services.dialogue
            publisher = SSEEventPublisher()
            tasks = DialogueTaskService(publisher)
            try:
                if job["kind"] == "dialogue":
                    return tasks._run(
                        service=service, user_id=user_id, task_id=job["job_id"], **payload
                    )
                return tasks._watch_discovery(
                    service, user_id, payload["task_id"], payload["session_id"], payload["initial"]
                )
            finally:
                tasks.close()
        raise ValueError("unknown durable job kind")
