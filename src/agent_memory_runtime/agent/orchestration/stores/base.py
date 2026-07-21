from __future__ import annotations

from typing import Protocol

from agent_memory_runtime.agent.orchestration.models import (
    DelegationRecord,
    OrchestrationRun,
)


class OrchestrationStateStore(Protocol):
    def create_run(self, run: OrchestrationRun) -> OrchestrationRun:
        ...

    def get_run(self, orchestration_id: str) -> OrchestrationRun | None:
        ...

    def get_run_by_request(
        self,
        tenant_id: str,
        request_id: str,
    ) -> OrchestrationRun | None:
        ...

    def claim_run(
        self,
        orchestration_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> OrchestrationRun | None:
        ...

    def renew_run(
        self,
        orchestration_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: float,
    ) -> bool:
        ...

    def update_run(
        self,
        run: OrchestrationRun,
        *,
        expected_version: int,
        lease_token: str | None = None,
    ) -> OrchestrationRun:
        ...

    def cancel_run(
        self,
        orchestration_id: str,
        *,
        tenant_id: str,
    ) -> OrchestrationRun:
        ...

    def create_delegations(
        self,
        records: tuple[DelegationRecord, ...],
    ) -> list[DelegationRecord]:
        ...

    def get_delegation(
        self,
        orchestration_id: str,
        task_id: str,
    ) -> DelegationRecord | None:
        ...

    def list_delegations(self, orchestration_id: str) -> list[DelegationRecord]:
        ...

    def update_delegation(
        self,
        record: DelegationRecord,
        *,
        expected_version: int,
    ) -> DelegationRecord:
        ...
