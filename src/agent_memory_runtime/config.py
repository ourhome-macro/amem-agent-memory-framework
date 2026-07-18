from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field

from agent_memory_runtime.audit.hashing import stable_hash


@dataclass(frozen=True)
class RetrievalWeights:
    keyword: float = 1.0
    recency: float = 0.2
    salience: float = 0.8
    confidence: float = 0.3
    type_boost: float = 0.4
    source_link: float = 0.6


@dataclass(frozen=True)
class ProviderPreset:
    provider: str
    base_url: str
    api_key_env: str
    default_model: str
    temperature: float | None = 0.2
    extra_body: dict[str, object] = field(default_factory=dict)


_PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-v4-flash",
    ),
    "openai": ProviderPreset(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-5-mini",
        temperature=None,
    ),
    "gemini": ProviderPreset(
        provider="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
        default_model="gemini-2.5-flash",
        temperature=None,
    ),
    "qwen": ProviderPreset(
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        default_model="qwen3.6-plus",
    ),
    "zai": ProviderPreset(
        provider="zai",
        base_url="https://api.z.ai/api/paas/v4/",
        api_key_env="ZAI_API_KEY",
        default_model="glm-5.2",
    ),
    "kimi": ProviderPreset(
        provider="kimi",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
        default_model="kimi-k2.6",
        temperature=None,
        extra_body={"thinking": {"type": "disabled"}},
    ),
}


def provider_presets() -> tuple[ProviderPreset, ...]:
    return tuple(_PROVIDER_PRESETS[name] for name in sorted(_PROVIDER_PRESETS))


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = "deepseek-v4-flash"
    temperature: float | None = 0.2
    max_tokens: int = 512
    timeout_seconds: float = 60.0
    extra_body: dict[str, object] = field(default_factory=dict)

    @classmethod
    def for_provider(
        cls,
        provider: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 512,
        timeout_seconds: float = 60.0,
        extra_body: dict[str, object] | None = None,
    ) -> LLMConfig:
        provider_id = provider.strip().casefold()
        if provider_id == "custom":
            if not base_url or not api_key_env or not model:
                raise ValueError(
                    "Custom provider requires --base-url, --api-key-env, and --model."
                )
            return cls(
                provider=provider_id,
                base_url=base_url,
                api_key_env=api_key_env,
                model=model,
                temperature=temperature if temperature is not None else 0.2,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                extra_body=deepcopy(extra_body) if extra_body is not None else {},
            )

        preset = _PROVIDER_PRESETS.get(provider_id)
        if preset is None:
            supported = ", ".join((*_PROVIDER_PRESETS, "custom"))
            raise ValueError(f"Unsupported provider '{provider}'. Use one of: {supported}.")
        return cls(
            provider=provider_id,
            base_url=base_url or preset.base_url,
            api_key_env=api_key_env or preset.api_key_env,
            model=model or preset.default_model,
            temperature=temperature if temperature is not None else preset.temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            extra_body=(
                deepcopy(extra_body) if extra_body is not None else deepcopy(preset.extra_body)
            ),
        )


@dataclass(frozen=True)
class RuntimeConfig:
    rule_version: str = "builtin-v1"
    max_retrieval_results: int = 8
    context_token_budget: int = 900
    low_salience_archive_threshold: float = 0.12
    retrieval_weights: RetrievalWeights = field(default_factory=RetrievalWeights)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def config_hash(self) -> str:
        return stable_hash(asdict(self))
