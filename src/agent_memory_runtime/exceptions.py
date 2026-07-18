class AgentMemoryRuntimeError(Exception):
    """Base error for runtime failures."""


class AccessDeniedError(AgentMemoryRuntimeError):
    """Raised when a principal cannot access a memory."""


class ConsistencyError(AgentMemoryRuntimeError):
    """Raised when replay output differs from an expected snapshot."""


class StoreError(AgentMemoryRuntimeError):
    """Raised when a store cannot persist or load data."""


class WriteGuardError(AgentMemoryRuntimeError):
    """Raised when a memory candidate violates information-flow constraints."""

