# RabbitMQ migration

> Follow-up: the active path now uses a transactional SQLite outbox and Celery v2
> queues. The raw Pika worker is an optional v1 drain bridge. See
> [reliability fixes](../../doc/production-reliability-fixes-2026-09-15.md) for current
> startup, recovery and verification instructions. The topology below records the
> initial migration, not the current v2 worker architecture.

## Outcome

The active Recommend Radio stack now uses RabbitMQ for asynchronous music-behavior delivery.
The obsolete RocketMQ broker configuration has been removed. This is a real application-path
migration: HTTP workers publish behavior events and a dedicated worker consumes them into AMEM.

## Runtime topology

```text
backend -> topic exchange recommend-radio.events.v1
        -> quorum queue recommend-radio.amem.behavior.v1
        -> behavior-worker -> AMEM gRPC

failed after delivery limit
        -> direct exchange recommend-radio.dlx.v1
        -> quorum queue recommend-radio.amem.behavior.dlq.v1
```

Messages are persistent and publishing uses broker confirms with mandatory routing. Each message
gets a stable `event_id` and `occurred_at`; AMEM preserves both values so RabbitMQ redelivery is
idempotent. The consumer acknowledges only after AMEM accepts the event. Malformed messages are
dead-lettered immediately; transient AMEM failures are requeued with bounded backoff and RabbitMQ's
quorum-queue delivery limit.

## One-command local start

From `recommend-radio`:

```powershell
.\start.ps1
```

The command starts the local embedding service, Docker Desktop when needed, RabbitMQ, AMEM, the
behavior worker, the HTTP/SSE services, and the frontend. It rebuilds application images and waits
for Compose health checks. For a subsequent start without rebuilding:

```powershell
.\start.ps1 -NoBuild
```

Useful endpoints:

- Application: `http://localhost:3000`
- RabbitMQ management: `http://127.0.0.1:15672`
- Backend readiness: `http://127.0.0.1:5000/health/ready`

The local management login comes from `RABBITMQ_USER` and `RABBITMQ_PASSWORD`. The Compose-only
development defaults are `radio` / `radio_dev_password`.

## Production configuration

Production startup requires explicit `RABBITMQ_USER` and `RABBITMQ_PASSWORD` values in the ignored
`.env` file. The production override removes both RabbitMQ host port mappings, including the
management UI. Rotate credentials through the deployment secret process; never commit `.env`.

The remaining supported tuning keys are documented in `.env.example`:

- `RABBITMQ_PREFETCH_COUNT` controls in-flight messages per worker.
- `RABBITMQ_DELIVERY_LIMIT` controls attempts before dead-lettering.
- Exchange, routing-key, queue, and DLQ names are versioned and independently configurable.

Inspect the stack and queues with:

```powershell
docker compose ps
docker compose logs --tail=200 behavior-worker rabbitmq
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
```

## Migration and rollback notes

RocketMQ data cannot be mounted into RabbitMQ. The active branch had already removed the historical
Go/RocketMQ publisher and consumer before this migration. This does not establish whether a deployed
RocketMQ instance still has a backlog; operators must check before switching production traffic.
Historical E2E log files still mention RocketMQ because they are immutable test evidence.

For rollback, set `RABBITMQ_ENABLED=false` only when running the backend outside this Compose file;
behavior writes then use the original synchronous AMEM bridge. The Compose stack intentionally sets
RabbitMQ on so a broker outage fails readiness and behavior writes instead of silently losing data.
