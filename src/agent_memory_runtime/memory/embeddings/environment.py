from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from agent_memory_runtime.exceptions import EmbeddingConfigurationError
from agent_memory_runtime.memory.embeddings.models import EmbeddingSpec
from agent_memory_runtime.memory.embeddings.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
)


@dataclass(frozen=True)
class EmbeddingEnvironment:
    provider: OpenAICompatibleEmbeddingProvider | None
    min_similarity: float | None


def load_embedding_environment(
    *,
    required_provider: bool = False,
    require_online_threshold: bool = False,
) -> EmbeddingEnvironment:
    """Load the optional OpenAI-compatible embedding deployment from the environment."""

    load_dotenv(override=False)
    model_id = (os.environ.get("AMEM_EMBEDDING_MODEL") or "").strip()
    if not model_id:
        if required_provider:
            raise EmbeddingConfigurationError("AMEM_EMBEDDING_MODEL is required")
        return EmbeddingEnvironment(provider=None, min_similarity=None)

    dimensions = _positive_int("AMEM_EMBEDDING_DIMENSIONS")
    timeout_seconds = _positive_float(
        "AMEM_EMBEDDING_TIMEOUT_SECONDS",
        default="30",
    )
    min_similarity = _optional_similarity("AMEM_EMBEDDING_MIN_SIMILARITY")
    if require_online_threshold and min_similarity is None:
        raise EmbeddingConfigurationError(
            "AMEM_EMBEDDING_MIN_SIMILARITY is required for online semantic retrieval"
        )
    spec = EmbeddingSpec(
        provider=os.environ.get("AMEM_EMBEDDING_PROVIDER", "openai-compatible"),
        model_id=model_id,
        model_revision=os.environ.get("AMEM_EMBEDDING_MODEL_REVISION", "default"),
        dimensions=dimensions,
        normalized=_env_bool("AMEM_EMBEDDING_NORMALIZED", default=True),
        query_prefix=os.environ.get("AMEM_EMBEDDING_QUERY_PREFIX", ""),
        document_prefix=os.environ.get("AMEM_EMBEDDING_DOCUMENT_PREFIX", ""),
        semantic_tag_allowlist=tuple(
            tag.strip()
            for tag in os.environ.get("AMEM_EMBEDDING_SEMANTIC_TAGS", "").split(",")
            if tag.strip()
        ),
        query_template_version=os.environ.get("AMEM_EMBEDDING_QUERY_TEMPLATE_VERSION", "v1"),
        document_template_version=os.environ.get("AMEM_EMBEDDING_DOCUMENT_TEMPLATE_VERSION", "v1"),
    )
    return EmbeddingEnvironment(
        provider=OpenAICompatibleEmbeddingProvider(
            spec,
            base_url=(
                os.environ.get("AMEM_EMBEDDING_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or "https://api.openai.com/v1"
            ),
            api_key_env=os.environ.get("AMEM_EMBEDDING_API_KEY_ENV", "OPENAI_API_KEY"),
            timeout_seconds=timeout_seconds,
            send_dimensions=_env_bool(
                "AMEM_EMBEDDING_SEND_DIMENSIONS",
                default=False,
            ),
        ),
        min_similarity=min_similarity,
    )


def _positive_int(name: str) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        raise EmbeddingConfigurationError(f"{name} is required")
    try:
        value = int(raw)
    except ValueError as error:
        raise EmbeddingConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise EmbeddingConfigurationError(f"{name} must be positive")
    return value


def _positive_float(name: str, *, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError as error:
        raise EmbeddingConfigurationError(f"{name} must be a float") from error
    if value <= 0:
        raise EmbeddingConfigurationError(f"{name} must be positive")
    return value


def _optional_similarity(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise EmbeddingConfigurationError(f"{name} must be a float") from error
    if not -1.0 <= value <= 1.0:
        raise EmbeddingConfigurationError(f"{name} must be between -1 and 1")
    return value


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EmbeddingConfigurationError(f"{name} must be a boolean")
