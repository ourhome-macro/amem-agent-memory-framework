from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from agent_memory_runtime.audit.dashboard import generate_audit_dashboard_html
from agent_memory_runtime.cli.audit import filter_audit_records
from agent_memory_runtime.cli.banner import BANNER
from agent_memory_runtime.config import (
    FastResponseConfig,
    LLMConfig,
    RuntimeConfig,
    provider_presets,
)
from agent_memory_runtime.domain.event import Event
from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.evals import evaluate_contains
from agent_memory_runtime.governance.queue import JsonlDerivationQueueStore
from agent_memory_runtime.governance.queue.worker import DerivationWorker
from agent_memory_runtime.governance.retention import (
    RetentionExecutor,
    RetentionPlanner,
    RetentionPolicy,
)
from agent_memory_runtime.memory.stores import (
    JsonlAuditStore,
    JsonlEventStore,
    JsonlMemoryStore,
    JsonlSnapshotStore,
)
from agent_memory_runtime.runtime import AgentMemoryRuntime, AgentResponse

app = typer.Typer(help="Agent Memory Runtime debugger.")
console = Console(markup=False)
DEFAULT_DATA_DIR = Path(".amem")
DATA_DIR_OPTION = typer.Option(help="Runtime data directory.")
PATH_OPTION = typer.Option(help="Runtime data directory.")
AGENT_OPTION = typer.Option("--agent")
QUERY_OPTION = typer.Option("--query")
SESSION_OPTION = typer.Option("--session")
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


@app.callback()
def print_startup_banner() -> None:
    print(BANNER)


@app.command()
def init(path: Annotated[Path, PATH_OPTION] = DEFAULT_DATA_DIR) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "events.jsonl").touch(exist_ok=True)
    (path / "memories.jsonl").touch(exist_ok=True)
    (path / "snapshots.jsonl").touch(exist_ok=True)
    (path / "audit.jsonl").touch(exist_ok=True)
    (path / "derivation_queue.jsonl").touch(exist_ok=True)
    console.print(f"initialized {path}")


@app.command()
def ingest(
    source: Path,
    async_derive: Annotated[
        bool,
        typer.Option("--async-derive", help="Only persist events and enqueue derivation jobs."),
    ] = False,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    count = 0
    for event in _load_events(source):
        if async_derive:
            runtime.ingest_async(event)
        else:
            runtime.ingest(event)
        count += 1
    _print_snapshot(runtime)
    console.print(f"ingested_events={count}")
    if async_derive:
        console.print(f"pending_derivation_jobs={runtime.derivation_queue.pending_count()}")


@app.command()
def derive(data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR) -> None:
    runtime = _runtime(data_dir)
    runtime.replay()
    _print_snapshot(runtime)


@app.command()
def retrieve(
    agent: Annotated[str, AGENT_OPTION],
    query: Annotated[str, QUERY_OPTION],
    session: Annotated[str | None, SESSION_OPTION] = None,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    records, trace = runtime.retrieve(MemoryQuery(agent_id=agent, text=query, session_id=session))
    _print_records(records)
    _print_trace(trace.to_dict())


@app.command()
def project(
    agent: Annotated[str, AGENT_OPTION],
    query: Annotated[str, QUERY_OPTION],
    session: Annotated[str | None, SESSION_OPTION] = None,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    context = runtime.project(MemoryQuery(agent_id=agent, text=query, session_id=session))
    console.print(context.projected_context)
    _print_trace(runtime.last_trace.to_dict())


@app.command()
def respond(
    agent: Annotated[str, AGENT_OPTION],
    query: Annotated[str, QUERY_OPTION],
    session: Annotated[str | None, SESSION_OPTION] = None,
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
            )
        ),
    )
    memory_query = MemoryQuery(agent_id=agent, text=query, session_id=session)
    if stream:
        response = _stream_response(runtime, memory_query, instruction=instruction, fast=fast)
    elif fast:
        response = runtime.respond_fast(memory_query, instruction=instruction)
        console.print(response.content)
    else:
        response = runtime.respond(memory_query, instruction=instruction)
        console.print(response.content)
    _print_trace({**runtime.last_trace.to_dict(), **response.to_dict()})


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


queue_app = typer.Typer(help="Derivation queue debugger.", invoke_without_command=True)
app.add_typer(queue_app, name="queue")


@queue_app.callback(invoke_without_command=True)
def queue_status(
    ctx: typer.Context,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    runtime = _runtime(data_dir)
    _print_trace(
        {
            "pending_derivation_jobs": runtime.derivation_queue.pending_count(),
            "jobs": [job.to_dict() for job in runtime.derivation_queue.list_jobs()],
        }
    )


@queue_app.command("run-once")
def queue_run_once(data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR) -> None:
    runtime = _runtime(data_dir)
    job = runtime.run_derivation_once()
    _print_trace(
        {
            "job": None if job is None else job.to_dict(),
            "pending_derivation_jobs": runtime.derivation_queue.pending_count(),
            "snapshot": runtime.snapshot().to_dict(),
        }
    )


@app.command("worker")
def worker(
    forever: Annotated[
        bool,
        typer.Option("--forever", help="Keep polling until interrupted."),
    ] = False,
    max_jobs: Annotated[int | None, typer.Option("--max-jobs")] = None,
    poll_interval_seconds: Annotated[
        float,
        typer.Option("--poll-interval-seconds"),
    ] = 1.0,
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    derivation_worker = DerivationWorker(
        runtime,
        poll_interval_seconds=poll_interval_seconds,
    )
    if forever:
        report = derivation_worker.run_forever(stop_after_jobs=max_jobs)
    else:
        report = derivation_worker.run_until_idle(max_jobs=max_jobs)
    _print_trace(
        {
            "processed": report.processed,
            "succeeded": report.succeeded,
            "failed": report.failed,
            "dead_lettered": report.dead_lettered,
            "pending_derivation_jobs": runtime.derivation_queue.pending_count(),
        }
    )


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
    ).apply(plan, snapshot=snapshot)
    runtime.snapshot_store.save(runtime.snapshot().to_dict())
    _print_trace(
        {
            "archived_memory_ids": list(report.archived_memory_ids),
            "deleted_memory_ids": list(report.deleted_memory_ids),
            "snapshot": runtime.snapshot().to_dict(),
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
    data_dir: Annotated[Path, DATA_DIR_OPTION] = DEFAULT_DATA_DIR,
) -> None:
    runtime = _runtime(data_dir)
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    table = Table("case_id", "passed", "expected", "selected")
    for case in payload.get("cases", []):
        query = MemoryQuery(
            agent_id=str(case["agent"]),
            text=str(case["query"]),
            session_id=case.get("session_id"),
        )
        context = runtime.project(query)
        result = evaluate_contains(
            str(case["id"]),
            [str(item) for item in case.get("expected_memory_ids", [])],
            list(context.selected_memory_ids),
        )
        table.add_row(
            result.case_id,
            str(result.passed),
            ", ".join(result.expected_memory_ids),
            ", ".join(result.selected_memory_ids),
        )
    console.print(table)


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


def _runtime(data_dir: Path, *, config: RuntimeConfig | None = None) -> AgentMemoryRuntime:
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_config = config or RuntimeConfig()
    return AgentMemoryRuntime(
        config=runtime_config,
        event_store=JsonlEventStore(data_dir / "events.jsonl"),
        memory_store=JsonlMemoryStore(data_dir / "memories.jsonl"),
        snapshot_store=JsonlSnapshotStore(data_dir / "snapshots.jsonl"),
        audit_store=JsonlAuditStore(data_dir / "audit.jsonl"),
        derivation_queue=JsonlDerivationQueueStore(data_dir / "derivation_queue.jsonl"),
    )


def _llm_config(
    *,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key_env: str | None,
) -> LLMConfig:
    try:
        return LLMConfig.for_provider(
            provider,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
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
    table = Table("memory_id", "type", "scope", "layer", "salience", "confidence")
    for record in records:
        table.add_row(
            record.memory_id,
            record.memory_type,
            record.scope,
            record.layer,
            f"{record.salience:.2f}",
            f"{record.confidence:.2f}",
        )
    console.print(table)


def _print_snapshot(runtime: AgentMemoryRuntime) -> None:
    _print_trace(runtime.snapshot().to_dict())


def _print_trace(payload: dict[str, object]) -> None:
    console.print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _examples_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "examples" / "data"
