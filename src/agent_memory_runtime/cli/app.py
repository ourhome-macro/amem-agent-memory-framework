from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from threading import Event as ThreadEvent
from time import perf_counter
from typing import Annotated, Any
from uuid import uuid4

import typer
import yaml
from rich.console import Console
from rich.table import Table

from agent_memory_runtime.audit.dashboard import generate_audit_dashboard_html
from agent_memory_runtime.cli.audit import filter_audit_records
from agent_memory_runtime.cli.banner import BANNER
from agent_memory_runtime.cli.chat import ChatMode, ChatSettings, run_chat
from agent_memory_runtime.config import (
    FastResponseConfig,
    HybridRetrievalConfig,
    LLMConfig,
    RetrievalWeights,
    RuntimeConfig,
    provider_presets,
)
from agent_memory_runtime.domain.enums import MemorySessionPolicy
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.evals import evaluate_retrieval
from agent_memory_runtime.exceptions import EmbeddingConfigurationError, StoreError
from agent_memory_runtime.governance.retention import (
    RetentionCycle,
    RetentionExecutor,
    RetentionPlanner,
    RetentionPolicy,
    RetentionWorker,
)
from agent_memory_runtime.memory.embeddings import (
    EmbeddingProvider,
    QdrantVectorIndex,
    VectorIndex,
    load_embedding_environment,
)
from agent_memory_runtime.memory.stores import SQLiteStoreBundle
from agent_memory_runtime.runtime import AgentMemoryRuntime, AgentResponse

app = typer.Typer(help="Agent Memory Runtime debugger.")
console = Console(markup=False)
DEFAULT_DATA_DIR = Path(".amem")
DATA_DIR_OPTION = typer.Option(help="Runtime data directory.")
PATH_OPTION = typer.Option(help="Runtime data directory.")
AGENT_OPTION = typer.Option("--agent")
QUERY_OPTION = typer.Option("--query")
SESSION_OPTION = typer.Option("--session")
TENANT_OPTION = typer.Option("--tenant", help="Tenant identity used for access control.")
USER_OPTION = typer.Option("--user", help="User identity used for access control.")
SESSION_POLICY_OPTION = typer.Option(
    "--session-policy",
    help="Memory session scope: exact, profile, or all.",
)
INSTRUCTION_OPTION = typer.Option("--instruction", help="Additional non-secret system instruction.")
PROVIDER_OPTION = typer.Option("--provider", help="OpenAI-compatible provider preset or custom.")
MODEL_OPTION = typer.Option("--model", help="Override the provider default model.")
BASE_URL_OPTION = typer.Option("--base-url", help="Override the provider base URL.")
API_KEY_ENV_OPTION = typer.Option(
    "--api-key-env",
    help="Environment variable that holds the API key.",
)
FAST_OPTION = typer.Option("--fast", help="Use snapshot-backed low-latency context.")
STREAM_OPTION = typer.Option("--stream", help="Stream assistant tokens as they arrive.")
RETRIEVAL_TIMEOUT_OPTION = typer.Option(
    "--retrieval-timeout-ms",
    help="Maximum full retrieval wait before snapshot fallback.",
)
NO_BANNER_OPTION = typer.Option(
    "--no-banner",
    help="Suppress the human startup banner for machine-readable output.",
)


@app.callback()
def print_startup_banner(
    ctx: typer.Context,
    no_banner: Annotated[bool, NO_BANNER_OPTION] = False,
) -> None:
    if not no_banner and ctx.invoked_subcommand != "chat":
        print(BANNER)


@app.command()
def init(path: Annotated[Path, PATH_OPTION] = DEFAULT_DATA_DIR) -> None:
    path.mkdir(parents=True, exist_ok=True)
    stores = SQLiteStoreBundle(path / "runtime.sqlite")
    console.print(f"initialized {path} database=runtime.sqlite schema={stores.schema_version}")


@app.command()
def ingest(
    source: Path,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    count = 0
    for event in _load_events(source):
        runtime.ingest(event)
        count += 1
    _print_snapshot(runtime)
    console.print(f"audited_events={count}")


@app.command()
def retrieve(
    agent: Annotated[str, AGENT_OPTION],
    query: Annotated[str, QUERY_OPTION],
    session: Annotated[str | None, SESSION_OPTION] = None,
    tenant: Annotated[str, TENANT_OPTION] = "default",
    user: Annotated[str | None, USER_OPTION] = None,
    session_policy: Annotated[
        MemorySessionPolicy,
        SESSION_POLICY_OPTION,
    ] = MemorySessionPolicy.EXACT,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    records, trace = runtime.retrieve(
        _memory_query(
            agent=agent,
            text=query,
            session=session,
            tenant=tenant,
            user=user,
            session_policy=session_policy,
        )
    )
    _print_records(records)
    _print_trace(trace.to_dict())


@app.command()
def project(
    agent: Annotated[str, AGENT_OPTION],
    query: Annotated[str, QUERY_OPTION],
    session: Annotated[str | None, SESSION_OPTION] = None,
    tenant: Annotated[str, TENANT_OPTION] = "default",
    user: Annotated[str | None, USER_OPTION] = None,
    session_policy: Annotated[
        MemorySessionPolicy,
        SESSION_POLICY_OPTION,
    ] = MemorySessionPolicy.EXACT,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    context = runtime.project(
        _memory_query(
            agent=agent,
            text=query,
            session=session,
            tenant=tenant,
            user=user,
            session_policy=session_policy,
        )
    )
    console.print(context.projected_context)
    _print_trace(runtime.last_trace.to_dict())


@app.command()
def respond(
    agent: Annotated[str, AGENT_OPTION],
    query: Annotated[str, QUERY_OPTION],
    session: Annotated[str | None, SESSION_OPTION] = None,
    tenant: Annotated[str, TENANT_OPTION] = "default",
    user: Annotated[str | None, USER_OPTION] = None,
    session_policy: Annotated[
        MemorySessionPolicy,
        SESSION_POLICY_OPTION,
    ] = MemorySessionPolicy.EXACT,
    instruction: Annotated[str | None, INSTRUCTION_OPTION] = None,
    provider: Annotated[str, PROVIDER_OPTION] = "deepseek",
    model: Annotated[str | None, MODEL_OPTION] = None,
    base_url: Annotated[str | None, BASE_URL_OPTION] = None,
    api_key_env: Annotated[str | None, API_KEY_ENV_OPTION] = None,
    fast: Annotated[bool, FAST_OPTION] = False,
    stream: Annotated[bool, STREAM_OPTION] = False,
    retrieval_timeout_ms: Annotated[int, RETRIEVAL_TIMEOUT_OPTION] = 150,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(
        data_dir,
        config=RuntimeConfig(
            fast_response=FastResponseConfig(retrieval_timeout_ms=retrieval_timeout_ms),
            llm=_llm_config(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
            ),
        ),
    )
    memory_query = _memory_query(
        agent=agent,
        text=query,
        session=session,
        tenant=tenant,
        user=user,
        session_policy=session_policy,
    )
    if stream:
        response = _stream_response(runtime, memory_query, instruction=instruction, fast=fast)
    elif fast:
        response = runtime.respond_fast(memory_query, instruction=instruction)
        console.print(response.content)
    else:
        response = runtime.respond(memory_query, instruction=instruction)
        console.print(response.content)
    _print_trace({**runtime.last_trace.to_dict(), **response.to_dict()})


@app.command()
def chat(
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", "-p", help="Run one turn instead of opening the shell."),
    ] = None,
    mode: Annotated[
        ChatMode,
        typer.Option("--mode", help="auto, interactive, text, or jsonl."),
    ] = ChatMode.AUTO,
    agent: Annotated[str, AGENT_OPTION] = "assistant",
    session: Annotated[str | None, SESSION_OPTION] = None,
    tenant: Annotated[str, TENANT_OPTION] = "default",
    user: Annotated[str | None, USER_OPTION] = None,
    instruction: Annotated[str | None, INSTRUCTION_OPTION] = None,
    provider: Annotated[str, PROVIDER_OPTION] = "deepseek",
    model: Annotated[str | None, MODEL_OPTION] = None,
    base_url: Annotated[str | None, BASE_URL_OPTION] = None,
    api_key_env: Annotated[str | None, API_KEY_ENV_OPTION] = None,
    max_tokens: Annotated[int, typer.Option("--max-tokens", min=1)] = 4096,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0.1),
    ] = 120.0,
    remember: Annotated[
        bool,
        typer.Option(
            "--remember/--no-remember",
            help="Persist completed turns into the memory event stream.",
        ),
    ] = True,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    try:
        settings = ChatSettings(
            agent_id=agent,
            session_id=session or f"cli-{uuid4().hex[:12]}",
            tenant_id=tenant,
            user_id=user,
            instruction=instruction,
            remember=remember,
        )
        config = _llm_config(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        exit_code = asyncio.run(
            run_chat(
                settings=settings,
                config=config,
                data_dir=data_dir,
                console=console,
                mode=mode,
                prompt=prompt,
            )
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    except KeyboardInterrupt as error:
        console.print("interrupted")
        raise typer.Exit(code=130) from error
    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command("providers")
def list_providers() -> None:
    table = Table("provider", "default_model", "api_key_env", "base_url")
    for preset in provider_presets():
        table.add_row(
            preset.provider,
            preset.default_model,
            preset.api_key_env,
            preset.base_url,
        )
    table.add_row("custom", "required", "required", "required")
    console.print(table)


@app.command("audit")
def show_audit(
    audit_type: Annotated[str | None, typer.Option("--type")] = None,
    outcome: Annotated[str | None, typer.Option("--outcome")] = None,
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    try:
        records = filter_audit_records(
            runtime.audit_store.list_envelopes(),
            audit_type=audit_type,
            outcome=outcome,
            subject=subject,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    traces = [trace.to_dict() for trace in runtime.audit_store.list_traces()]
    _print_trace(
        {
            "audit_records": [record.to_dict() for record in records],
            "llm_call_traces": traces,
        }
    )


@app.command("audit-dashboard")
def audit_dashboard(
    out: Annotated[Path, typer.Option("--out", help="HTML dashboard output path.")],
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        generate_audit_dashboard_html(runtime.audit_store.list_envelopes()),
        encoding="utf-8",
    )
    console.print(f"audit_dashboard={out}")

embedding_app = typer.Typer(help="sqlite-vec embedding index operations.")
app.add_typer(embedding_app, name="embedding")


@embedding_app.command("status")
def embedding_status(
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    stores = SQLiteStoreBundle(data_dir / "runtime.sqlite")
    provider = _embedding_provider_from_env(required=False)
    status = stores.semantic_status()
    configured_generation = None if provider is None else provider.spec.generation
    status.update(
        {
            "configured_provider_generation": configured_generation,
            "semantic_available": bool(
                configured_generation and configured_generation == status["active_generation"]
            ),
        }
    )
    _print_trace(status)


@embedding_app.command("backfill")
def embedding_backfill(
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    provider = _embedding_provider_from_env(required=True)
    if provider is None:  # pragma: no cover - narrowed by required=True
        raise typer.BadParameter("embedding provider is not configured")
    stores = SQLiteStoreBundle(data_dir / "runtime.sqlite")
    stores.embedding_generations.register(provider.spec, status="backfill")
    scheduled = stores.enqueue_embedding_backfill()
    _print_trace(
        {
            "generation": provider.spec.generation,
            "scheduled_jobs": scheduled,
            "pending_jobs": stores.embedding_jobs.pending_count(
                generation=provider.spec.generation
            ),
        }
    )


@embedding_app.command("worker")
def embedding_worker(
    forever: Annotated[
        bool,
        typer.Option("--forever", help="Keep polling until interrupted."),
    ] = False,
    max_jobs: Annotated[int | None, typer.Option("--max-jobs", min=1)] = None,
    poll_interval_seconds: Annotated[
        float,
        typer.Option("--poll-interval-seconds", min=0.01),
    ] = 1.0,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    provider = _embedding_provider_from_env(required=True)
    if provider is None:  # pragma: no cover - narrowed by required=True
        raise typer.BadParameter("embedding provider is not configured")
    stores = SQLiteStoreBundle(data_dir / "runtime.sqlite")
    stores.embedding_generations.register(provider.spec, status="backfill")
    stores.enqueue_embedding_backfill()
    embedding = stores.embedding_worker(
        provider,
        poll_interval_seconds=poll_interval_seconds,
    )
    if forever:
        report = embedding.run_forever(stop_after_jobs=max_jobs)
    else:
        report = embedding.run_until_idle(max_jobs=max_jobs)
    _print_trace(
        {
            "generation": provider.spec.generation,
            "processed": report.processed,
            "succeeded": report.succeeded,
            "failed": report.failed,
            "dead_lettered": report.dead_lettered,
            "superseded": report.superseded,
            "embedding_coverage": stores.vector_index.coverage(generation=provider.spec.generation),
            "pending_jobs": stores.embedding_jobs.pending_count(
                generation=provider.spec.generation
            ),
        }
    )


@embedding_app.command("activate")
def embedding_activate(
    generation: Annotated[str | None, typer.Option("--generation")] = None,
    minimum_coverage: Annotated[
        float,
        typer.Option("--minimum-coverage", min=0.0, max=1.0),
    ] = 1.0,
    allow_pending_jobs: Annotated[
        bool,
        typer.Option(
            "--allow-pending-jobs",
            help="Emergency override; normal production activation must drain jobs.",
        ),
    ] = False,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    target = generation
    if target is None:
        provider = _embedding_provider_from_env(required=True)
        target = None if provider is None else provider.spec.generation
    if target is None:  # pragma: no cover - narrowed by required=True
        raise typer.BadParameter("--generation or embedding provider config is required")
    stores = SQLiteStoreBundle(data_dir / "runtime.sqlite")
    try:
        activated = stores.activate_embedding_generation(
            target,
            minimum_coverage=minimum_coverage,
            allow_pending_jobs=allow_pending_jobs,
        )
    except StoreError as error:
        raise typer.BadParameter(str(error)) from error
    _print_trace(
        {
            "active_generation": activated.generation,
            "embedding_coverage": stores.vector_index.coverage(generation=target),
        }
    )


@embedding_app.command("prune")
def embedding_prune(
    generation: Annotated[str, typer.Option("--generation")],
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    stores = SQLiteStoreBundle(data_dir / "runtime.sqlite")
    try:
        stores.delete_retired_embedding_generation(generation)
    except StoreError as error:
        raise typer.BadParameter(str(error)) from error
    _print_trace({"deleted_retired_generation": generation})


retention_app = typer.Typer(help="Retention policy debugger.")
app.add_typer(retention_app, name="retention")


def _retention_policy(
    archive_after: int,
    archive_below_salience: float | None,
    delete_sensitive_after: int | None,
) -> RetentionPolicy:
    return RetentionPolicy(
        archive_working_after_sequences=archive_after,
        archive_below_salience=archive_below_salience,
        delete_sensitive_after_sequences=delete_sensitive_after,
    )


@retention_app.command("plan")
def retention_plan(
    archive_after: Annotated[int, typer.Option("--archive-after-seq")] = 30,
    archive_below_salience: Annotated[
        float | None,
        typer.Option("--archive-below-salience"),
    ] = None,
    delete_sensitive_after: Annotated[
        int | None,
        typer.Option("--delete-sensitive-after-seq"),
    ] = None,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    plan = RetentionPlanner(
        _retention_policy(archive_after, archive_below_salience, delete_sensitive_after)
    ).plan(
        runtime.memory_store.list_records(),
        current_sequence=runtime.snapshot().last_event_sequence,
    )
    _print_trace(
        {
            "current_sequence": plan.current_sequence,
            "actions": [
                {
                    "memory_id": action.memory_id,
                    "action": action.action,
                    "reason": action.reason,
                }
                for action in plan.actions
            ],
        }
    )


@retention_app.command("apply")
def retention_apply(
    archive_after: Annotated[int, typer.Option("--archive-after-seq")] = 30,
    archive_below_salience: Annotated[
        float | None,
        typer.Option("--archive-below-salience"),
    ] = None,
    delete_sensitive_after: Annotated[
        int | None,
        typer.Option("--delete-sensitive-after-seq"),
    ] = None,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    snapshot = runtime.snapshot()
    plan = RetentionPlanner(
        _retention_policy(archive_after, archive_below_salience, delete_sensitive_after)
    ).plan(runtime.memory_store.list_records(), current_sequence=snapshot.last_event_sequence)
    report = RetentionExecutor(
        memory_store=runtime.memory_store,
        audit_store=runtime.audit_store,
        tombstone_store=runtime.tombstone_store,
        transaction_manager=runtime.transaction_manager,
    ).apply(plan, snapshot=snapshot)
    runtime.refresh_snapshot()
    _print_trace(
        {
            "archived_memory_ids": list(report.archived_memory_ids),
            "deleted_memory_ids": list(report.deleted_memory_ids),
            "snapshot": runtime.snapshot().to_dict(),
        }
    )


@retention_app.command("worker")
def retention_worker(
    forever: Annotated[
        bool,
        typer.Option("--forever", help="Run retention cycles until interrupted."),
    ] = False,
    max_cycles: Annotated[int | None, typer.Option("--max-cycles", min=1)] = None,
    interval_seconds: Annotated[
        float,
        typer.Option("--interval-seconds", min=0.1),
    ] = 300.0,
    archive_after: Annotated[int, typer.Option("--archive-after-seq", min=0)] = 30,
    archive_below_salience: Annotated[
        float | None,
        typer.Option("--archive-below-salience", min=0, max=1),
    ] = None,
    delete_sensitive_after: Annotated[
        int | None,
        typer.Option("--delete-sensitive-after-seq", min=0),
    ] = None,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    retention = RetentionWorker(
        runtime,
        policy=_retention_policy(
            archive_after,
            archive_below_salience,
            delete_sensitive_after,
        ),
        interval_seconds=interval_seconds,
    )
    if not forever:
        cycle = retention.run_once()
        _print_retention_cycle(cycle)
        return

    stop_event = ThreadEvent()
    try:
        report = retention.run_forever(
            stop_event=stop_event,
            max_cycles=max_cycles,
            on_cycle=_print_retention_cycle,
        )
    except KeyboardInterrupt:
        stop_event.set()
        console.print("retention_worker=interrupted")
        return
    _print_trace(
        {
            "cycles": report.cycles,
            "archived": report.archived,
            "deleted": report.deleted,
        }
    )


@app.command()
def replay(data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR) -> None:
    runtime = _runtime(data_dir)
    before = runtime.snapshot()
    after = runtime.replay()
    _print_trace(
        {
            "rule_version": after.rule_version,
            "config_hash": after.config_hash,
            "last_event_sequence": after.last_event_sequence,
            "state_hash": after.state_hash,
            "consistent_with_previous_snapshot": before.state_hash == after.state_hash,
        }
    )


@app.command("eval")
def eval_cases(
    source: Path,
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="lexical-only, semantic-only, hybrid-rrf, or hybrid-business.",
        ),
    ] = "hybrid-business",
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir, config=_retrieval_eval_config(mode))
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    table = Table(
        "case_id",
        "passed",
        "R@K",
        "P@K",
        "MRR",
        "nDCG",
        "ms",
        "selected",
    )
    results = []
    latency_samples: list[float] = []
    semantic_queries = 0
    coverage_samples: list[float] = []
    for case in payload.get("cases", []):
        query = MemoryQuery(
            agent_id=str(case["agent"]),
            text=str(case["query"]),
            session_id=case.get("session_id"),
            tenant_id=str(case.get("tenant_id") or "default"),
            user_id=(None if case.get("user_id") is None else str(case["user_id"])),
            session_policy=str(case.get("session_policy") or "exact"),
            limit=int(case["limit"]) if case.get("limit") is not None else None,
        )
        started = perf_counter()
        context = runtime.project(query)
        latency_ms = (perf_counter() - started) * 1_000
        latency_samples.append(latency_ms)
        semantic_queries += int("semantic" in runtime.last_trace.retrieval_legs)
        if runtime.last_trace.embedding_coverage is not None:
            coverage_samples.append(runtime.last_trace.embedding_coverage)
        result = evaluate_retrieval(
            str(case["id"]),
            [str(item) for item in case.get("expected_memory_ids", [])],
            list(context.selected_memory_ids),
            forbidden=[str(item) for item in case.get("forbidden_memory_ids", [])],
            relevance={
                str(key): float(value) for key, value in dict(case.get("relevance") or {}).items()
            },
            k=int(case.get("k") or query.limit or runtime.config.max_retrieval_results),
        )
        table.add_row(
            result.case_id,
            str(result.passed),
            f"{result.recall_at_k:.3f}",
            f"{result.precision_at_k:.3f}",
            f"{result.reciprocal_rank:.3f}",
            f"{result.ndcg_at_k:.3f}",
            f"{latency_ms:.2f}",
            ", ".join(result.selected_memory_ids),
        )
        results.append(result)
    console.print(table)
    passed = sum(result.passed for result in results)
    _print_trace(
        {
            "cases": len(results),
            "mode": mode,
            "passed": passed,
            "failed": len(results) - passed,
            "mean_recall_at_k": _mean([result.recall_at_k for result in results]),
            "mean_precision_at_k": _mean([result.precision_at_k for result in results]),
            "mean_reciprocal_rank": _mean([result.reciprocal_rank for result in results]),
            "mean_ndcg_at_k": _mean([result.ndcg_at_k for result in results]),
            "forbidden_hit_count": sum(result.forbidden_hit_count for result in results),
            "no_result_accuracy": _mean(
                [float(result.no_result_correct) for result in results if result.no_result_case]
            ),
            "latency_p50_ms": _percentile(latency_samples, 0.50),
            "latency_p95_ms": _percentile(latency_samples, 0.95),
            "latency_p99_ms": _percentile(latency_samples, 0.99),
            "semantic_query_count": semantic_queries,
            "mean_embedding_coverage": _mean(coverage_samples),
        }
    )
    if passed != len(results):
        raise typer.Exit(code=1)


demo_app = typer.Typer(help="Bundled demos.")
app.add_typer(demo_app, name="demo")


@demo_app.command("customer-support")
def demo_customer_support() -> None:
    _run_demo("customer_support_events.jsonl", "support_agent", "refund status")


@demo_app.command("personal-assistant")
def demo_personal_assistant() -> None:
    _run_demo("personal_assistant_events.jsonl", "assistant_agent", "travel preference")


@demo_app.command("mock-interviewer")
def demo_mock_interviewer() -> None:
    _run_demo("mock_interviewer_events.jsonl", "interviewer_agent", "system design interview")


def _run_demo(filename: str, agent: str, query: str) -> None:
    runtime = AgentMemoryRuntime()
    for event in _load_events(_examples_dir() / filename):
        runtime.ingest(event)
    context = runtime.project(MemoryQuery(agent_id=agent, text=query))
    console.print(context.projected_context)
    _print_trace(runtime.last_trace.to_dict())


def _stream_response(
    runtime: AgentMemoryRuntime,
    query: MemoryQuery,
    *,
    instruction: str | None,
    fast: bool,
) -> AgentResponse:
    response: AgentResponse | None = None
    for event in runtime.respond_stream(query, instruction=instruction, fast_path=fast):
        if event.type == "token":
            console.print(event.delta, end="")
        elif event.type == "completed":
            response = event.response
    console.print()
    if response is None:
        raise RuntimeError("Streaming response did not complete.")
    return response


def _memory_query(
    *,
    agent: str,
    text: str,
    session: str | None,
    tenant: str,
    user: str | None,
    session_policy: MemorySessionPolicy,
) -> MemoryQuery:
    return MemoryQuery(
        agent_id=agent,
        text=text,
        session_id=session,
        tenant_id=tenant,
        user_id=user,
        session_policy=session_policy.value,
    )


def _print_retention_cycle(cycle: RetentionCycle) -> None:
    _print_trace(
        {
            "current_sequence": cycle.plan.current_sequence,
            "planned_actions": len(cycle.plan.actions),
            "archived_memory_ids": list(cycle.report.archived_memory_ids),
            "deleted_memory_ids": list(cycle.report.deleted_memory_ids),
            "snapshot": cycle.snapshot.to_dict(),
        }
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _retrieval_eval_config(mode: str) -> RuntimeConfig:
    normalized = mode.strip().casefold()
    if normalized == "lexical-only":
        return RuntimeConfig(hybrid_retrieval=HybridRetrievalConfig(enable_semantic=False))
    if normalized == "semantic-only":
        return RuntimeConfig(
            hybrid_retrieval=HybridRetrievalConfig(
                enable_lexical=False,
                allow_uncalibrated_semantic=False,
            )
        )
    if normalized == "hybrid-rrf":
        return RuntimeConfig(
            retrieval_weights=RetrievalWeights(
                keyword=1.0,
                semantic=1.0,
                fusion=2.0,
                recency=0.0,
                salience=0.0,
                confidence=0.0,
                type_boost=0.0,
                source_link=0.0,
            )
        )
    if normalized == "hybrid-business":
        return RuntimeConfig()
    raise typer.BadParameter(
        "--mode must be lexical-only, semantic-only, hybrid-rrf, or hybrid-business"
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile)))
    return round(ordered[index], 4)


def _runtime(
    data_dir: Path,
    *,
    config: RuntimeConfig | None = None,
) -> AgentMemoryRuntime:
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_config = config or RuntimeConfig()
    try:
        embedding_environment = load_embedding_environment()
    except EmbeddingConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    embedding_provider = embedding_environment.provider
    vector_index = (
        None
        if embedding_provider is None
        else _vector_index_from_environment(embedding_environment)
    )
    if embedding_provider is not None and runtime_config.hybrid_retrieval.enable_semantic:
        if (
            runtime_config.hybrid_retrieval.min_semantic_similarity is None
            and embedding_environment.min_similarity is not None
        ):
            runtime_config = replace(
                runtime_config,
                hybrid_retrieval=replace(
                    runtime_config.hybrid_retrieval,
                    min_semantic_similarity=embedding_environment.min_similarity,
                ),
            )
        if (
            runtime_config.hybrid_retrieval.min_semantic_similarity is None
            and not runtime_config.hybrid_retrieval.allow_uncalibrated_semantic
        ):
            raise typer.BadParameter(
                "AMEM_EMBEDDING_MIN_SIMILARITY is required for online semantic retrieval"
            )
    try:
        stores = SQLiteStoreBundle(
            data_dir / "runtime.sqlite",
            embedding_provider=embedding_provider,
            vector_index=vector_index,
        )
    except StoreError as error:
        raise typer.BadParameter(str(error)) from error
    return AgentMemoryRuntime(
        config=runtime_config,
        event_store=stores.event_store,
        memory_store=stores.memory_store,
        snapshot_store=stores.snapshot_store,
        audit_store=stores.audit_store,
        tombstone_store=stores.tombstone_store,
        dream_store=stores.dream_store,
        transaction_manager=stores,
    )


def _vector_index_from_environment(embedding_environment: object) -> VectorIndex | None:
    if getattr(embedding_environment, "vector_backend", "sqlite") != "qdrant":
        return None
    return QdrantVectorIndex(
        collection_name=str(getattr(embedding_environment, "qdrant_collection", "agent_memory")),
        url=getattr(embedding_environment, "qdrant_url", None),
        api_key=getattr(embedding_environment, "qdrant_api_key", None),
    )


def _embedding_provider_from_env(
    *,
    required: bool,
) -> EmbeddingProvider | None:
    try:
        return load_embedding_environment(required_provider=required).provider
    except EmbeddingConfigurationError as error:
        raise typer.BadParameter(str(error)) from error


def _llm_config(
    *,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key_env: str | None,
    max_tokens: int = 512,
    timeout_seconds: float = 60.0,
) -> LLMConfig:
    try:
        return LLMConfig.for_provider(
            provider,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


def _load_events(path: Path) -> list[Event]:
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(Event.from_dict(json.loads(line)))
    return events


def _print_records(records: list[Any]) -> None:
    table = Table("memory_id", "type", "level", "visibility", "status", "priority")
    for record in records:
        table.add_row(
            record.memory_id,
            record.memory_type,
            record.level,
            record.visibility,
            record.status,
            f"{record.priority:.2f}",
        )
    console.print(table)


def _print_snapshot(runtime: AgentMemoryRuntime) -> None:
    _print_trace(runtime.snapshot().to_dict())


def _print_trace(payload: dict[str, object]) -> None:
    console.print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _examples_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "examples" / "data"
