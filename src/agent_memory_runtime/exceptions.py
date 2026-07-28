class AgentMemoryRuntimeError(Exception):
    """Base error for runtime failures."""


class AccessDeniedError(AgentMemoryRuntimeError):
    """Raised when a principal cannot access a memory."""


class ConsistencyError(AgentMemoryRuntimeError):
    """Raised when replay output differs from an expected snapshot."""


class StoreError(AgentMemoryRuntimeError):
    """Raised when a store cannot persist or load data."""


class EventConflictError(StoreError):
    """Raised when an event id is reused with a different canonical payload."""


class LeaseLostError(StoreError):
    """Raised when a worker attempts to mutate a job after losing its lease."""


class LLMConfigurationError(AgentMemoryRuntimeError):
    """Raised when the LLM provider configuration is incomplete or invalid."""


class LLMRequestError(AgentMemoryRuntimeError):
    """Raised when an LLM provider request cannot be completed."""


class LLMResponseError(AgentMemoryRuntimeError):
    """Raised when an LLM provider response has no usable assistant content."""


class EmbeddingConfigurationError(AgentMemoryRuntimeError):
    """Raised when an embedding provider is incomplete or incompatible."""


class EmbeddingDimensionError(AgentMemoryRuntimeError):
    """Raised when a provider returns a vector with an unexpected dimension."""


class SemanticCircuitOpenError(AgentMemoryRuntimeError):
    """Raised while the semantic provider circuit breaker is open."""
