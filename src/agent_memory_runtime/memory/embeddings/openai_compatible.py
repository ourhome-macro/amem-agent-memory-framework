from __future__ import annotations

import os
from typing import Any

from agent_memory_runtime.exceptions import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
)
from agent_memory_runtime.memory.embeddings.base import validate_vector
from agent_memory_runtime.memory.embeddings.models import EmbeddingSpec


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        spec: EmbeddingSpec,
        *,
        base_url: str,
        api_key_env: str,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        send_dimensions: bool = False,
        client: Any | None = None,
    ) -> None:
        self._spec = spec
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.send_dimensions = send_dimensions
        self._client = client

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.spec.format_query(text)])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed([self.spec.format_document(text) for text in texts])

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        parameters: dict[str, object] = {"model": self.spec.model_id, "input": inputs}
        if self.send_dimensions:
            parameters["dimensions"] = self.spec.dimensions
        response = self._get_client().embeddings.create(**parameters)
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(inputs))):
            raise EmbeddingDimensionError(
                "embedding provider returned invalid or duplicate item indexes"
            )
        vectors = [[float(value) for value in item.embedding] for item in ordered]
        if len(vectors) != len(inputs):
            raise EmbeddingDimensionError("embedding provider returned the wrong batch size")
        for vector in vectors:
            validate_vector(vector, self.spec)
        return vectors

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = self.api_key or os.environ.get(self.api_key_env)
        if not api_key:
            self._load_dotenv()
            api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise EmbeddingConfigurationError(
                f"Missing embedding API key environment variable: {self.api_key_env}"
            )
        try:
            from openai import OpenAI
        except ImportError as error:
            raise EmbeddingConfigurationError(
                "The OpenAI SDK is unavailable. Run: pip install -e ."
            ) from error
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        return self._client

    @staticmethod
    def _load_dotenv() -> None:
        try:
            from dotenv import load_dotenv
        except ImportError as error:
            raise EmbeddingConfigurationError(
                "The .env loader is unavailable. Run: pip install -e ."
            ) from error
        load_dotenv(override=False)
