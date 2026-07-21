from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent_memory_runtime.agent import (
    AgentRequest,
    AgentRunEvent,
    BusinessAgentRuntime,
    OpenAICompatibleModelGateway,
)
from agent_memory_runtime.config import LLMConfig, RuntimeConfig, provider_presets
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime


class ChatMode(StrEnum):
    AUTO = "auto"
    INTERACTIVE = "interactive"
    TEXT = "text"
    JSONL = "jsonl"


@dataclass
class ChatSettings:
    agent_id: str
    session_id: str
    tenant_id: str = "default"
    user_id: str | None = None
    instruction: str | None = None
    remember: bool = True

    def __post_init__(self) -> None:
        if not self.agent_id.strip() or not self.session_id.strip():
            raise ValueError("agent_id and session_id cannot be empty")
        if not self.tenant_id.strip():
            raise ValueError("tenant_id cannot be empty")


@dataclass
class ChatRuntimeContext:
    config: LLMConfig
    bundle: SQLiteStoreBundle
    memory_runtime: AgentMemoryRuntime
    runtime: BusinessAgentRuntime

    def reconfigure(self, config: LLMConfig) -> None:
        self.config = config
        self.runtime = BusinessAgentRuntime(
            model_gateway=OpenAICompatibleModelGateway(config),
            memory_runtime=self.memory_runtime,
            state_store=self.bundle.agent_state_store,
        )


@dataclass(frozen=True)
class ChatTurnResult:
    run_id: str
    status: str
    output: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0

    @property
    def exit_code(self) -> int:
        if self.status == "completed":
            return 0
        if self.status in {"approval_required", "reconciliation_required"}:
            return 2
        return 1


def build_chat_context(data_dir: Path, config: LLMConfig) -> ChatRuntimeContext:
    data_dir.mkdir(parents=True, exist_ok=True)
    bundle = SQLiteStoreBundle(data_dir / "runtime.sqlite")
    memory_runtime = AgentMemoryRuntime(
        config=RuntimeConfig(llm=config),
        event_store=bundle.event_store,
        memory_store=bundle.memory_store,
        snapshot_store=bundle.snapshot_store,
        audit_store=bundle.audit_store,
        derivation_queue=bundle.derivation_queue,
        transaction_manager=bundle,
    )
    runtime = BusinessAgentRuntime(
        model_gateway=OpenAICompatibleModelGateway(config),
        memory_runtime=memory_runtime,
        state_store=bundle.agent_state_store,
    )
    return ChatRuntimeContext(
        config=config,
        bundle=bundle,
        memory_runtime=memory_runtime,
        runtime=runtime,
    )


async def run_chat(
    *,
    settings: ChatSettings,
    config: LLMConfig,
    data_dir: Path,
    console: Console,
    mode: ChatMode = ChatMode.AUTO,
    prompt: str | None = None,
    context_factory: Callable[[Path, LLMConfig], ChatRuntimeContext] | None = None,
) -> int:
    resolved_mode = _resolve_mode(mode, prompt=prompt)
    if resolved_mode is not ChatMode.INTERACTIVE and not (prompt or "").strip():
        raise ValueError("--prompt is required in text and jsonl modes")
    factory = context_factory or build_chat_context
    context = factory(data_dir, config)

    if resolved_mode is not ChatMode.INTERACTIVE:
        result = await _run_turn(
            context,
            settings,
            (prompt or "").strip(),
            mode=resolved_mode,
            console=console,
        )
        return result.exit_code

    _print_welcome(context, settings, console)
    pending_prompt = (prompt or "").strip() or None
    while True:
        try:
            message = pending_prompt or typer.prompt("you", prompt_suffix=" › ")
        except (EOFError, typer.Abort):
            console.print("bye")
            return 0
        pending_prompt = None
        message = message.strip()
        if not message:
            continue
        if message.startswith("//"):
            message = message[1:]
        elif message.startswith("/"):
            should_exit = _handle_command(
                message,
                context=context,
                settings=settings,
                console=console,
            )
            if should_exit:
                return 0
            continue
        await _run_turn(
            context,
            settings,
            message,
            mode=ChatMode.INTERACTIVE,
            console=console,
        )


async def _run_turn(
    context: ChatRuntimeContext,
    settings: ChatSettings,
    message: str,
    *,
    mode: ChatMode,
    console: Console,
) -> ChatTurnResult:
    request_id = str(uuid4())
    request = AgentRequest(
        agent_id=settings.agent_id,
        message=message,
        actor_id=settings.user_id or "user",
        session_id=settings.session_id,
        tenant_id=settings.tenant_id,
        user_id=settings.user_id,
        request_id=request_id,
        instructions=(
            () if settings.instruction is None else (settings.instruction,)
        ),
        metadata={"adapter": "amem-cli"},
    )
    started_at = perf_counter()
    run_id = ""
    status = "failed"
    output = ""
    input_tokens = 0
    output_tokens = 0
    printed_delta = False
    iterator = context.runtime.run(request)

    while True:
        waiting: AgentRunEvent | None = None
        async for event in iterator:
            run_id = event.run_id
            _render_event(event, mode=mode, console=console)
            if event.type == "model.output.delta":
                printed_delta = True
            elif event.type == "run.completed":
                status = "completed"
                output = str(event.data.get("output") or "")
                input_tokens = int(event.data.get("input_tokens") or 0)
                output_tokens = int(event.data.get("output_tokens") or 0)
            elif event.type == "run.failed":
                status = "failed"
            elif event.type == "run.cancelled":
                status = "cancelled"
            elif event.type == "approval.required":
                status = "approval_required"
                waiting = event
            elif event.type == "tool.reconciliation_required":
                status = "reconciliation_required"
                waiting = event

        if waiting is None or mode is not ChatMode.INTERACTIVE:
            break
        if waiting.type == "approval.required":
            approved = typer.confirm(
                f"approve tool {waiting.data.get('tool_name', 'unknown')}?",
                default=False,
            )
            await context.runtime.decide_approval(
                str(waiting.data["approval_id"]),
                tenant_id=settings.tenant_id,
                reviewer_id=settings.user_id or "cli-user",
                approved=approved,
                reason="interactive CLI decision",
            )
        else:
            resolution = typer.prompt(
                "tool outcome [succeeded/failed/abort]",
                default="abort",
            ).strip().casefold()
            if resolution == "abort":
                break
            if resolution not in {"succeeded", "failed"}:
                console.print("invalid outcome; leaving the run paused")
                break
            reconciliation_output: dict[str, Any] = {}
            if resolution == "succeeded":
                reconciliation_output = _prompt_json_object(console)
            await context.runtime.reconcile_tool_call(
                str(waiting.data["call_id"]),
                tenant_id=settings.tenant_id,
                reviewer_id=settings.user_id or "cli-user",
                succeeded=resolution == "succeeded",
                output=reconciliation_output,
            )
        iterator = context.runtime.resume(
            run_id,
            tenant_id=settings.tenant_id,
            user_id=settings.user_id,
        )

    elapsed_ms = round((perf_counter() - started_at) * 1000)
    result = ChatTurnResult(
        run_id=run_id,
        status=status,
        output=output,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        elapsed_ms=elapsed_ms,
    )
    if mode in {ChatMode.TEXT, ChatMode.INTERACTIVE}:
        if status == "completed" and not printed_delta:
            console.print(output)
        elif printed_delta:
            console.print()
        if mode is ChatMode.INTERACTIVE:
            _print_turn_status(result, context.config, console)
    if settings.remember and status == "completed":
        _remember_turn(
            context,
            settings,
            request_id=request_id,
            message=message,
            output=output,
            mode=mode,
            console=console,
        )
    return result


def _render_event(
    event: AgentRunEvent,
    *,
    mode: ChatMode,
    console: Console,
) -> None:
    if mode is ChatMode.JSONL:
        _print_jsonl(
            console,
            event.to_dict(),
        )
        return
    if event.type == "model.output.delta":
        console.print(
            str(event.data.get("delta") or ""),
            end="",
            highlight=False,
            soft_wrap=True,
        )
    elif event.type == "approval.required":
        console.print(
            f"approval required: {event.data.get('tool_name', 'unknown')} "
            f"({event.data.get('call_id', 'unknown')})"
        )
    elif event.type == "tool.reconciliation_required":
        console.print(
            f"manual reconciliation required: "
            f"{event.data.get('tool_name', 'unknown')} "
            f"({event.data.get('call_id', 'unknown')})"
        )
    elif event.type in {"run.failed", "run.cancelled", "run.lease_lost"}:
        console.print(
            f"{event.type}: {event.data.get('error_type', event.data.get('retryable', ''))}"
        )


def _handle_command(
    raw: str,
    *,
    context: ChatRuntimeContext,
    settings: ChatSettings,
    console: Console,
) -> bool:
    command, _, argument = raw.partition(" ")
    name = command.casefold()
    argument = argument.strip()
    if name in {"/exit", "/quit"}:
        console.print("bye")
        return True
    if name == "/help":
        _print_help(console)
    elif name == "/status":
        _print_status(context, settings, console)
    elif name == "/providers":
        _print_providers(context.config.provider, console)
    elif name == "/model":
        if not argument:
            console.print(
                f"model={context.config.model} provider={context.config.provider}"
            )
        else:
            config = LLMConfig.for_provider(
                context.config.provider,
                model=argument,
                base_url=context.config.base_url,
                api_key_env=context.config.api_key_env,
                temperature=context.config.temperature,
                max_tokens=context.config.max_tokens,
                timeout_seconds=context.config.timeout_seconds,
                extra_body=context.config.extra_body,
            )
            context.reconfigure(config)
            console.print(f"model={config.model}")
    elif name == "/provider":
        if not argument:
            console.print(f"provider={context.config.provider}")
        else:
            try:
                config = LLMConfig.for_provider(
                    argument,
                    max_tokens=context.config.max_tokens,
                    timeout_seconds=context.config.timeout_seconds,
                )
            except ValueError as error:
                console.print(str(error))
            else:
                context.reconfigure(config)
                console.print(
                    f"provider={config.provider} model={config.model}"
                )
    elif name == "/session":
        if argument:
            settings.session_id = argument
        console.print(f"session={settings.session_id}")
    elif name == "/new":
        settings.session_id = argument or f"cli-{uuid4().hex[:12]}"
        console.print(f"session={settings.session_id}")
    elif name == "/history":
        _print_history(context, settings, argument=argument, console=console)
    else:
        console.print(f"unknown command: {command}; use /help")
    return False


def _print_welcome(
    context: ChatRuntimeContext,
    settings: ChatSettings,
    console: Console,
) -> None:
    console.print(
        f"interactive agent · {context.config.provider}/{context.config.model} · "
        f"agent={settings.agent_id} · session={settings.session_id}"
    )
    console.print("type /help for commands, //text to send a leading slash")


def _print_help(console: Console) -> None:
    table = Table("command", "action")
    for command, action in (
        ("/status", "show active provider, model and identity"),
        ("/providers", "list provider presets"),
        ("/model [id]", "show or switch the model"),
        ("/provider [id]", "show or switch the provider preset"),
        ("/session [id]", "show or switch the durable session"),
        ("/new [id]", "start a new session"),
        ("/history [n]", "show recent sanitized chat events"),
        ("/exit", "leave interactive mode"),
    ):
        table.add_row(command, action)
    console.print(table)


def _print_status(
    context: ChatRuntimeContext,
    settings: ChatSettings,
    console: Console,
) -> None:
    table = Table("field", "value")
    for key, value in (
        ("provider", context.config.provider),
        ("model", context.config.model),
        ("agent", settings.agent_id),
        ("tenant", settings.tenant_id),
        ("user", settings.user_id or "-"),
        ("session", settings.session_id),
        ("remember", str(settings.remember).lower()),
    ):
        table.add_row(key, value)
    console.print(table)


def _print_providers(current: str, console: Console) -> None:
    table = Table("provider", "default model", "selected")
    for preset in provider_presets():
        table.add_row(
            preset.provider,
            preset.default_model,
            "yes" if preset.provider == current else "",
        )
    console.print(table)


def _print_history(
    context: ChatRuntimeContext,
    settings: ChatSettings,
    *,
    argument: str,
    console: Console,
) -> None:
    try:
        limit = 20 if not argument else max(1, min(100, int(argument)))
    except ValueError:
        console.print("history limit must be an integer between 1 and 100")
        return
    events = [
        event
        for event in context.memory_runtime.event_store.list_events()
        if event.session_id == settings.session_id and "cli-chat" in event.tags
    ][-limit:]
    if not events:
        console.print("no chat history for this session")
        return
    table = Table("actor", "message")
    for event in events:
        table.add_row(event.actor_id, str(event.payload.get("text") or ""))
    console.print(table)


def _print_turn_status(
    result: ChatTurnResult,
    config: LLMConfig,
    console: Console,
) -> None:
    console.print(
        f"[{result.status}] {config.provider}/{config.model} · "
        f"tokens {result.input_tokens}+{result.output_tokens} · "
        f"{result.elapsed_ms} ms · run {result.run_id}"
    )


def _remember_turn(
    context: ChatRuntimeContext,
    settings: ChatSettings,
    *,
    request_id: str,
    message: str,
    output: str,
    mode: ChatMode,
    console: Console,
) -> None:
    user_event = Event(
        event_id=f"cli-chat:user:{request_id}",
        kind="message.created",
        actor_id=settings.user_id or "user",
        session_id=settings.session_id,
        tenant_id=settings.tenant_id,
        user_id=settings.user_id,
        agent_id=settings.agent_id,
        labels=("private",),
        tags=("cli-chat", "user"),
        payload={
            "agent_id": settings.agent_id,
            "user_id": settings.user_id,
            "tenant_id": settings.tenant_id,
            "subject_id": settings.user_id or "user",
            "text": message,
        },
    )
    assistant_event = Event(
        event_id=f"cli-chat:assistant:{request_id}",
        kind="message.created",
        actor_id=settings.agent_id,
        session_id=settings.session_id,
        tenant_id=settings.tenant_id,
        user_id=settings.user_id,
        agent_id=settings.agent_id,
        labels=("private",),
        tags=("cli-chat", "assistant"),
        caused_by_event_id=user_event.event_id,
        payload={
            "agent_id": settings.agent_id,
            "user_id": settings.user_id,
            "tenant_id": settings.tenant_id,
            "subject_id": settings.user_id or "user",
            "text": output,
        },
    )
    try:
        with context.bundle.transaction():
            context.memory_runtime.ingest(user_event)
            context.memory_runtime.ingest(assistant_event)
    except Exception as error:
        if mode is ChatMode.JSONL:
            _print_jsonl(
                console,
                {
                    "type": "cli.memory_failed",
                    "data": {"error_type": type(error).__name__},
                },
            )
        else:
            console.print(f"memory write failed: {type(error).__name__}")


def _prompt_json_object(console: Console) -> dict[str, Any]:
    raw = typer.prompt("tool result JSON", default="{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        console.print("invalid JSON; using an empty object")
        return {}
    if not isinstance(value, dict):
        console.print("tool result must be a JSON object; using an empty object")
        return {}
    return {str(key): item for key, item in value.items()}


def _resolve_mode(mode: ChatMode, *, prompt: str | None) -> ChatMode:
    if mode is not ChatMode.AUTO:
        return mode
    return ChatMode.TEXT if (prompt or "").strip() else ChatMode.INTERACTIVE


def _print_jsonl(console: Console, value: dict[str, Any]) -> None:
    console.print(
        Text(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        highlight=False,
        soft_wrap=True,
    )
