from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Protocol

from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.exceptions import EmbeddingDimensionError
from agent_memory_runtime.memory.embeddings.models import (
    EmbeddingSpec,
    VectorHit,
    VectorRecord,
)


class EmbeddingProvider(Protocol):
    @property
    def spec(self) -> EmbeddingSpec: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class VectorIndex(Protocol):
    def upsert(self, record: VectorRecord) -> None: ...

    def search(
        self,
        vector: list[float],
        query: MemoryQuery,
        *,
        spec: EmbeddingSpec,
        limit: int,
    ) -> list[VectorHit]: ...

    def delete_memory(
        self,
        memory_id: str,
        *,
        through_sequence: int | None = None,
    ) -> None: ...

    def coverage(self, *, generation: str) -> float: ...


class CallableEmbeddingProvider:
    """Small adapter for local services and deterministic test providers."""

    def __init__(
        self,
        spec: EmbeddingSpec,
        *,
        query_embedder: Callable[[str], list[float]],
        document_embedder: Callable[[list[str]], list[list[float]]],
    ) -> None:
        self._spec = spec
        self._query_embedder = query_embedder
        self._document_embedder = document_embedder

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    def embed_query(self, text: str) -> list[float]:
        vector = self._query_embedder(self.spec.format_query(text))
        _validate_dimensions([vector], self.spec)
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        formatted = [self.spec.format_document(text) for text in texts]
        vectors = self._document_embedder(formatted)
        if len(vectors) != len(texts):
            raise EmbeddingDimensionError("embedding provider returned the wrong batch size")
        _validate_dimensions(vectors, self.spec)
        return vectors


def validate_vector(vector: list[float] | tuple[float, ...], spec: EmbeddingSpec) -> None:
    if len(vector) != spec.dimensions:
        raise EmbeddingDimensionError(
            f"embedding dimension mismatch: expected {spec.dimensions}, got {len(vector)}"
        )
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
        for value in vector
    ):
        raise EmbeddingDimensionError("embedding vectors must contain only finite numbers")
    if not any(float(value) != 0.0 for value in vector):
        raise EmbeddingDimensionError("embedding vectors cannot be all zero")


def _validate_dimensions(vectors: list[list[float]], spec: EmbeddingSpec) -> None:
    for vector in vectors:
        validate_vector(vector, spec)
