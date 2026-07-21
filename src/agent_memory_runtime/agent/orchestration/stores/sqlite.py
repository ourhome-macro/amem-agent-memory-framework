from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent_memory_runtime.agent.errors import (
    AgentIdentityError,
    AgentRunConflictError,
    AgentRunNotFoundError,
)
from agent_memory_runtime.agent.models import utc_now_iso
from agent_memory_runtime.agent.orchestration.models import (
    DelegationRecord,
    OrchestrationRun,
    OrchestrationStatus,
)
from agent_memory_runtime.agent.stores.codec import JsonStateCodec, StateCodec
from agent_memory_runtime.memory.stores.sqlite_manager import SQLiteTransactionManager


class SQLiteOrchestrationStore:
    """Durable DAG state with optimistic updates and fenced execution leases."""

    def __init__(
        self,
        path_or_manager: str | Path | SQLiteTransactionManager,
        *,
        codec: StateCodec | None = None,
    ) -> None:
        self._manager = (
            path_or_manager
            if isinstance(path_or_manager, SQLiteTransactionManager)
            else SQLiteTransactionManager(path_or_manager)
        )
        self.path = self._manager.path
        self._codec = codec or JsonStateCodec()

    def create_run(self, run: OrchestrationRun) -> OrchestrationRun:
        with self._manager.connection() as connection:
            row = connection.execute(
                """
                SELECT payload FROM agent_orchestrations
                WHERE tenant_id = ? AND request_id = ?
                """,
                (run.tenant_id, run.request.request_id),
            ).fetchone()
            if row is not None:
                existing = OrchestrationRun.from_dict(self._codec.decode(row[0]))
                if existing.request.to_dict() != run.request.to_dict():
                    raise AgentRunConflictError(
                        "request_id is already bound to a different orchestration request"
                    )
                return existing
            if connection.execute(
                "SELECT 1 FROM agent_orchestrations WHERE orchestration_id = ?",
                (run.orchestration_id,),
            ).fetchone():
                raise AgentRunConflictError(
                    f"orchestration_id {run.orchestration_id!r} already exists"
                )
            connection.execute(
                """
                INSERT INTO agent_orchestrations(
                    orchestration_id, tenant_id, request_id, status, version, payload
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run.orchestration_id,
                    run.tenant_id,
                    run.request.request_id,
                    run.status.value,
                    run.version,
                    self._codec.encode(run.to_dict()),
                ),
            )
        return run

    def get_run(self, orchestration_id: str) -> OrchestrationRun | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                "SELECT payload FROM agent_orchestrations WHERE orchestration_id = ?",
                (orchestration_id,),
            ).fetchone()
        return (
            None
            if row is None
            else OrchestrationRun.from_dict(self._codec.decode(row[0]))
        )

    def get_run_by_request(
        self,
        tenant_id: str,
        request_id: str,
    ) -> OrchestrationRun | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                """
                SELECT payload FROM agent_orchestrations
                WHERE tenant_id = ? AND request_id = ?
                """,
                (tenant_id, request_id),
            ).fetchone()
        return (
            None
            if row is None
            else OrchestrationRun.from_dict(self._codec.decode(row[0]))
        )

    def claim_run(
        self,
        orchestration_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> OrchestrationRun | None:
        with self._manager.connection() as connection:
            run = self._load_run_row(connection, orchestration_id)
            if run is None or run.is_terminal or run.status is OrchestrationStatus.WAITING:
                return None
            if run.status is OrchestrationStatus.RUNNING and not _lease_expired(run):
                return None
            now = datetime.now(UTC)
            claimed = replace(
                run,
                status=OrchestrationStatus.RUNNING,
                version=run.version + 1,
                error_type=None,
                updated_at=now.isoformat(),
                lease_owner=worker_id,
                lease_token=str(uuid4()),
                lease_expires_at=(now + timedelta(seconds=lease_seconds)).isoformat(),
            )
            self._update_run_row(connection, claimed)
        return claimed

    def renew_run(
        self,
        orchestration_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> bool:
        with self._manager.connection() as connection:
            run = self._load_run_row(connection, orchestration_id)
            if (
                run is None
                or run.status is not OrchestrationStatus.RUNNING
                or run.lease_owner != worker_id
                or run.lease_token != lease_token
                or _lease_expired(run)
            ):
                return False
            now = datetime.now(UTC)
            renewed = replace(
                run,
                updated_at=now.isoformat(),
                lease_expires_at=(now + timedelta(seconds=lease_seconds)).isoformat(),
            )
            self._update_run_row(connection, renewed)
        return True

    def update_run(
        self,
        run: OrchestrationRun,
        *,
        expected_version: int,
        lease_token: str | None = None,
    ) -> OrchestrationRun:
        with self._manager.connection() as connection:
            current = self._load_run_row(connection, run.orchestration_id)
            if current is None:
                raise AgentRunNotFoundError(
                    f"orchestration {run.orchestration_id!r} was not found"
                )
            if current.version != expected_version:
                raise AgentRunConflictError("orchestration version changed concurrently")
            if current.request.to_dict() != run.request.to_dict():
                raise AgentRunConflictError("orchestration request is immutable")
            if lease_token is not None and (
                current.lease_token != lease_token or _lease_expired(current)
            ):
                raise AgentRunConflictError("orchestration lease is no longer valid")
            stored = _next_run_version(run, current=current)
            self._update_run_row(connection, stored)
        return stored

    def cancel_run(
        self,
        orchestration_id: str,
        *,
        tenant_id: str,
    ) -> OrchestrationRun:
        with self._manager.connection() as connection:
            run = self._load_run_row(connection, orchestration_id)
            if run is None:
                raise AgentRunNotFoundError(
                    f"orchestration {orchestration_id!r} was not found"
                )
            if run.tenant_id != tenant_id:
                raise AgentIdentityError(
                    "orchestration does not belong to the requested tenant"
                )
            if run.is_terminal:
                return run
            cancelled = replace(
                run,
                status=OrchestrationStatus.CANCELLED,
                version=run.version + 1,
                error_type="AgentCancelledError",
                updated_at=utc_now_iso(),
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            self._update_run_row(connection, cancelled)
        return cancelled

    def create_delegations(
        self,
        records: tuple[DelegationRecord, ...],
    ) -> list[DelegationRecord]:
        with self._manager.connection() as connection:
            stored: list[DelegationRecord] = []
            for record in records:
                if not connection.execute(
                    "SELECT 1 FROM agent_orchestrations WHERE orchestration_id = ?",
                    (record.orchestration_id,),
                ).fetchone():
                    raise AgentRunNotFoundError(
                        f"orchestration {record.orchestration_id!r} was not found"
                    )
                row = connection.execute(
                    """
                    SELECT payload FROM agent_delegations
                    WHERE orchestration_id = ? AND task_id = ?
                    """,
                    (record.orchestration_id, record.task_id),
                ).fetchone()
                if row is not None:
                    existing = DelegationRecord.from_dict(self._codec.decode(row[0]))
                    if _delegation_identity(existing) != _delegation_identity(record):
                        raise AgentRunConflictError(
                            f"delegation {record.task_id!r} has conflicting identity"
                        )
                    stored.append(existing)
                    continue
                connection.execute(
                    """
                    INSERT INTO agent_delegations(
                        orchestration_id, task_id, agent_id, status, version, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.orchestration_id,
                        record.task_id,
                        record.agent_id,
                        record.status.value,
                        record.version,
                        self._codec.encode(record.to_dict()),
                    ),
                )
                stored.append(record)
        return stored

    def get_delegation(
        self,
        orchestration_id: str,
        task_id: str,
    ) -> DelegationRecord | None:
        with self._manager.read_connection() as connection:
            row = connection.execute(
                """
                SELECT payload FROM agent_delegations
                WHERE orchestration_id = ? AND task_id = ?
                """,
                (orchestration_id, task_id),
            ).fetchone()
        return (
            None
            if row is None
            else DelegationRecord.from_dict(self._codec.decode(row[0]))
        )

    def list_delegations(self, orchestration_id: str) -> list[DelegationRecord]:
        with self._manager.read_connection() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM agent_delegations
                WHERE orchestration_id = ? ORDER BY rowid
                """,
                (orchestration_id,),
            ).fetchall()
        return [DelegationRecord.from_dict(self._codec.decode(row[0])) for row in rows]

    def update_delegation(
        self,
        record: DelegationRecord,
        *,
        expected_version: int,
    ) -> DelegationRecord:
        with self._manager.connection() as connection:
            row = connection.execute(
                """
                SELECT payload FROM agent_delegations
                WHERE orchestration_id = ? AND task_id = ?
                """,
                (record.orchestration_id, record.task_id),
            ).fetchone()
            if row is None:
                raise AgentRunNotFoundError(
                    f"delegation {record.task_id!r} was not found"
                )
            current = DelegationRecord.from_dict(self._codec.decode(row[0]))
            if current.version != expected_version:
                raise AgentRunConflictError("delegation version changed concurrently")
            if _delegation_identity(current) != _delegation_identity(record):
                raise AgentRunConflictError("delegation identity is immutable")
            stored = replace(
                record,
                version=current.version + 1,
                updated_at=utc_now_iso(),
            )
            connection.execute(
                """
                UPDATE agent_delegations
                SET status = ?, version = ?, payload = ?
                WHERE orchestration_id = ? AND task_id = ? AND version = ?
                """,
                (
                    stored.status.value,
                    stored.version,
                    self._codec.encode(stored.to_dict()),
                    stored.orchestration_id,
                    stored.task_id,
                    expected_version,
                ),
            )
        return stored

    def _load_run_row(
        self,
        connection: object,
        orchestration_id: str,
    ) -> OrchestrationRun | None:
        row = connection.execute(
            "SELECT payload FROM agent_orchestrations WHERE orchestration_id = ?",
            (orchestration_id,),
        ).fetchone()
        return (
            None
            if row is None
            else OrchestrationRun.from_dict(self._codec.decode(row[0]))
        )

    def _update_run_row(self, connection: object, run: OrchestrationRun) -> None:
        connection.execute(
            """
            UPDATE agent_orchestrations
            SET status = ?, version = ?, payload = ?
            WHERE orchestration_id = ?
            """,
            (
                run.status.value,
                run.version,
                self._codec.encode(run.to_dict()),
                run.orchestration_id,
            ),
        )


def _next_run_version(
    run: OrchestrationRun,
    *,
    current: OrchestrationRun,
) -> OrchestrationRun:
    active = run.status is OrchestrationStatus.RUNNING
    return replace(
        run,
        version=current.version + 1,
        updated_at=utc_now_iso(),
        lease_owner=current.lease_owner if active else None,
        lease_token=current.lease_token if active else None,
        lease_expires_at=current.lease_expires_at if active else None,
    )


def _lease_expired(run: OrchestrationRun) -> bool:
    if not run.lease_expires_at:
        return True
    try:
        expires_at = datetime.fromisoformat(run.lease_expires_at)
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _delegation_identity(record: DelegationRecord) -> tuple[str, str, str]:
    return record.orchestration_id, record.task_id, record.agent_id
