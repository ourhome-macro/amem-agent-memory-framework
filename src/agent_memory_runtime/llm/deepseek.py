from __future__ import annotations

import os
from typing import Any

from agent_memory_runtime.config import LLMConfig
from agent_memory_runtime.exceptions import LLMConfigurationError, LLMRequestError, LLMResponseError
from agent_memory_runtime.llm.models import LLMResponse


class DeepSeekChatClient:
    """OpenAI-compatible, non-streaming client for the DeepSeek chat endpoint."""

    def __init__(self, config: LLMConfig, *, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    def complete(self, *, system_prompt: str, user_prompt: str) -> LLMResponse:
        try:
            response = self._get_client().chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                stream=False,
            )
        except (LLMConfigurationError, LLMResponseError):
            raise
        except Exception as error:
            raise LLMRequestError(
                f"DeepSeek completion request failed: {type(error).__name__}."
            ) from error
        if not response.choices:
            raise LLMResponseError("DeepSeek returned no completion choices.")

        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("DeepSeek returned an empty assistant message.")

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=content.strip(),
            model=str(getattr(response, "model", None) or self.config.model),
            response_id=_optional_str(getattr(response, "id", None)),
            input_tokens=_optional_int(getattr(usage, "prompt_tokens", None)),
            output_tokens=_optional_int(getattr(usage, "completion_tokens", None)),
        )

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
            raise LLMConfigurationError("DeepSeek base_url must use HTTPS.")

        try:
            from openai import OpenAI
        except ImportError as error:
            raise LLMConfigurationError(
                "The optional OpenAI-compatible client is unavailable. Run: pip install -e ."
            ) from error

        try:
            self._client = OpenAI(
                api_key=api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout_seconds,
            )
        except Exception as error:
            raise LLMConfigurationError("Could not initialize the DeepSeek client.") from error
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
        raise LLMRequestError("DeepSeek returned an invalid token usage value.") from error
