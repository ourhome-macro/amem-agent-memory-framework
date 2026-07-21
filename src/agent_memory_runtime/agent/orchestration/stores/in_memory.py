from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import RLock
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


class InMemoryOrchestrationStore:
    def __init__(self) -> None:
        self._runs: dict[str, OrchestrationRun] = {}
        self._request_runs: dict[tuple[str, str], str] = {}
        self._delegations: dict[tuple[str, str], DelegationRecord] = {}
        self._lock = RLock()

    def create_run(self, run: OrchestrationRun) -> OrchestrationRun:
        with self._lock:
            request_key = (run.tenant_id, run.request.request_id)
            existing_id = self._request_runs.get(request_key)
            if existing_id is not None:
                existing = self._runs[existing_id]
                if existing.request.to_dict() != run.request.to_dict():
                    raise AgentRunConflictError(
                        "request_id is already bound to a different orchestration request"
                    )
                return _clone_run(existing)
            if run.orchestration_id in self._runs:
                raise AgentRunConflictError(
                    f"orchestration_id {run.orchestration_id!r} already exists"
                )
            stored = _clone_run(run)
            self._runs[stored.orchestration_id] = stored
            self._request_runs[request_key] = stored.orchestration_id
            return _clone_run(stored)

    def get_run(self, orchestration_id: str) -> OrchestrationRun | None:
        with self._lock:
            run = self._runs.get(orchestration_id)
            return None if run is None else _clone_run(run)

    def get_run_by_request(
        self,
        tenant_id: str,
        request_id: str,
    ) -> OrchestrationRun | None:
        with self._lock:
            run_id = self._request_runs.get((tenant_id, request_id))
            if run_id is None:
                return None
            return _clone_run(self._runs[run_id])

    def claim_run(
        self,
        orchestration_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> OrchestrationRun | None:
        with self._lock:
            run = self._runs.get(orchestration_id)
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
            self._runs[orchestration_id] = claimed
            return _clone_run(claimed)

    def renew_run(
        self,
        orchestration_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> bool:
        with self._lock:
            run = self._runs.get(orchestration_id)
            if (
                run is None
                or run.status is not OrchestrationStatus.RUNNING
                or run.lease_owner != worker_id
                or run.lease_token != lease_token
                or _lease_expired(run)
            ):
                return False
            now = datetime.now(UTC)
            self._runs[orchestration_id] = replace(
                run,
                updated_at=now.isoformat(),
                lease_expires_at=(now + timedelta(seconds=lease_seconds)).isoformat(),
            )
            return True

    def update_run(
        self,
        run: OrchestrationRun,
        *,
        expected_version: int,
        lease_token: str | None = None,
    ) -> OrchestrationRun:
        with self._lock:
            current = self._runs.get(run.orchestration_id)
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
            self._runs[run.orchestration_id] = stored
            return _clone_run(stored)

    def cancel_run(
        self,
        orchestration_id: str,
        *,
        tenant_id: str,
    ) -> OrchestrationRun:
        with self._lock:
            run = self._runs.get(orchestration_id)
            if run is None:
                raise AgentRunNotFoundError(
                    f"orchestration {orchestration_id!r} was not found"
                )
            if run.tenant_id != tenant_id:
                raise AgentIdentityError(
                    "orchestration does not belong to the requested tenant"
                )
            if run.is_terminal:
                return _clone_run(run)
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
            self._runs[orchestration_id] = cancelled
            return _clone_run(cancelled)

    def create_delegations(
        self,
        records: tuple[DelegationRecord, ...],
    ) -> list[DelegationRecord]:
        with self._lock:
            for record in records:
                if record.orchestration_id not in self._runs:
                    raise AgentRunNotFoundError(
                        f"orchestration {record.orchestration_id!r} was not found"
                    )
                key = (record.orchestration_id, record.task_id)
                existing = self._delegations.get(key)
                if existing is not None and _delegation_identity(existing) != (
                    _delegation_identity(record)
                ):
                    raise AgentRunConflictError(
                        f"delegation {record.task_id!r} has conflicting identity"
                    )
            for record in records:
                key = (record.orchestration_id, record.task_id)
                self._delegations.setdefault(key, _clone_delegation(record))
            return [
                _clone_delegation(self._delegations[(item.orchestration_id, item.task_id)])
                for item in records
            ]

    def get_delegation(
        self,
        orchestration_id: str,
        task_id: str,
    ) -> DelegationRecord | None:
        with self._lock:
            record = self._delegations.get((orchestration_id, task_id))
            return None if record is None else _clone_delegation(record)

    def list_delegations(self, orchestration_id: str) -> list[DelegationRecord]:
        with self._lock:
            return [
                _clone_delegation(record)
                for (run_id, _), record in self._delegations.items()
                if run_id == orchestration_id
            ]

    def update_delegation(
        self,
        record: DelegationRecord,
        *,
        expected_version: int,
    ) -> DelegationRecord:
        with self._lock:
            key = (record.orchestration_id, record.task_id)
            current = self._delegations.get(key)
            if current is None:
                raise AgentRunNotFoundError(
                    f"delegation {record.task_id!r} was not found"
                )
            if current.version != expected_version:
                raise AgentRunConflictError("delegation version changed concurrently")
            if _delegation_identity(current) != _delegation_identity(record):
                raise AgentRunConflictError("delegation identity is immutable")
            stored = replace(
                record,
                version=current.version + 1,
                updated_at=utc_now_iso(),
            )
            self._delegations[key] = stored
            return _clone_delegation(stored)


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


def _clone_run(run: OrchestrationRun) -> OrchestrationRun:
    return OrchestrationRun.from_dict(run.to_dict())


def _clone_delegation(record: DelegationRecord) -> DelegationRecord:
    return DelegationRecord.from_dict(record.to_dict())
