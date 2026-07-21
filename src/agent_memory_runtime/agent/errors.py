from __future__ import annotations

from agent_memory_runtime.exceptions import AgentMemoryRuntimeError


class AgentRunError(AgentMemoryRuntimeError):
    """Base error for the business-agent orchestration layer."""


class AgentRunConflictError(AgentRunError):
    """Raised when optimistic state or request idempotency checks fail."""


class AgentRunNotFoundError(AgentRunError):
    """Raised when a requested durable run cannot be found."""


class AgentIdentityError(AgentRunError):
    """Raised when a caller attempts to cross a run identity boundary."""


class AgentPolicyError(AgentRunError):
    """Raised when a run or tool action violates its effective policy."""


class AgentApprovalError(AgentRunError):
    """Raised for invalid or conflicting approval decisions."""


class AgentCancelledError(AgentRunError):
    """Raised when a running agent is cancelled."""


class AgentLeaseLostError(AgentRunError):
    """Raised when another worker owns the durable run lease."""


class AgentReconciliationRequired(AgentRunError):
    """Raised when a side effect has an unknown outcome and cannot be retried safely."""


class ModelProtocolError(AgentRunError):
    """Raised when a model gateway returns an invalid provider-neutral response."""
