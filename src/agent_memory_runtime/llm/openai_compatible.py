from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from agent_memory_runtime.config import LLMConfig
from agent_memory_runtime.exceptions import LLMConfigurationError, LLMRequestError, LLMResponseError
from agent_memory_runtime.llm.models import LLMResponse, LLMStreamEvent


class OpenAICompatibleChatClient:
    """OpenAI Chat Completions client with non-streaming and streaming modes."""

    def __init__(self, config: LLMConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        try:
            response = self._get_client().chat.completions.create(
                **self._request(system_prompt=system_prompt, user_prompt=user_prompt, stream=False)
            )
        except (LLMConfigurationError, LLMResponseError):
            raise
        except Exception as error:
            raise LLMRequestError(
                f"{self.config.provider} completion request failed: {type(error).__name__}."
            ) from error
        if not response.choices:
            raise LLMResponseError(f"{self.config.provider} returned no completion choices.")

        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError(
                f"{self.config.provider} returned an empty assistant message."
            )

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=content.strip(),
            model=str(getattr(response, "model", None) or self.config.model),
            response_id=_optional_str(getattr(response, "id", None)),
            input_tokens=_optional_int(getattr(usage, "prompt_tokens", None)),
            output_tokens=_optional_int(getattr(usage, "completion_tokens", None)),
        )

    def stream_complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> Iterator[LLMStreamEvent]:
        try:
            chunks = self._get_client().chat.completions.create(
                **self._request(system_prompt=system_prompt, user_prompt=user_prompt, stream=True)
            )
        except (LLMConfigurationError, LLMResponseError):
            raise
        except Exception as error:
            raise LLMRequestError(
                f"{self.config.provider} streaming request failed: {type(error).__name__}."
            ) from error

        model = self.config.model
        response_id: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        emitted_content = False
        for chunk in chunks:
            response_id = _optional_str(getattr(chunk, "id", response_id))
            model = str(getattr(chunk, "model", None) or model)
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                input_tokens = _optional_int(getattr(usage, "prompt_tokens", None))
                output_tokens = _optional_int(getattr(usage, "completion_tokens", None))

            for delta in _chunk_deltas(chunk):
                emitted_content = True
                yield LLMStreamEvent(
                    type="token",
                    delta=delta,
                    model=model,
                    response_id=response_id,
                )

        if not emitted_content:
            raise LLMResponseError(
                f"{self.config.provider} returned an empty streaming assistant message."
            )
        yield LLMStreamEvent(
            type="completed",
            model=model,
            response_id=response_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _request(self, *, system_prompt: str, user_prompt: str, stream: bool) -> dict[str, object]:
        request: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": stream,
        }
        if self.config.temperature is not None:
            request["temperature"] = self.config.temperature
        if self.config.max_tokens > 0:
            request["max_tokens"] = self.config.max_tokens
        if self.config.extra_body:
            request["extra_body"] = self.config.extra_body
        return request

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            self._load_dotenv()
            api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise LLMConfigurationError(
                f"Missing {self.config.api_key_env}. Set it before calling runtime.respond()."
            )
        if not self.config.base_url.startswith("https://"):
            raise LLMConfigurationError(f"{self.config.provider} base_url must use HTTPS.")

        try:
            from openai import OpenAI
        except ImportError as error:
            raise LLMConfigurationError(
                "The OpenAI-compatible client is unavailable. Run: pip install -e ."
            ) from error

        try:
            self._client = OpenAI(
                api_key=api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout_seconds,
            )
        except Exception as error:
            raise LLMConfigurationError(
                f"Could not initialize the {self.config.provider} client."
            ) from error
        return self._client

    @staticmethod
    def _load_dotenv() -> None:
        try:
            from dotenv import load_dotenv
        except ImportError as error:
            raise LLMConfigurationError(
                "The .env loader is unavailable. Run: pip install -e ."
            ) from error
        load_dotenv(override=False)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise LLMRequestError("Provider returned an invalid token usage value.") from error


def _chunk_deltas(chunk: object) -> list[str]:
    deltas: list[str] = []
    for choice in getattr(chunk, "choices", ()) or ():
        delta = getattr(choice, "delta", None)
        content = getattr(delta, "content", None)
        if isinstance(content, str) and content:
            deltas.append(content)
    return deltas
