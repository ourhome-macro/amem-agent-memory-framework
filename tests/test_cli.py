from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_memory_runtime.cli.app import app


def test_cli_ingest_retrieve_project_and_replay(tmp_path) -> None:
    runner = CliRunner()
    data_dir = tmp_path / ".amem"
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_id": "evt-cli-1",
                "kind": "message.created",
                "actor_id": "customer",
                "session_id": "cli-s1",
                "labels": ["private"],
                "tags": ["refund"],
                "payload": {
                    "agent_id": "support_agent",
                    "subject_id": "order",
                    "text": "Refund status needs gateway confirmation.",
                    "salience": 0.8,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert runner.invoke(app, ["init", "--path", str(data_dir)]).exit_code == 0
    assert runner.invoke(app, ["ingest", str(events), "--data-dir", str(data_dir)]).exit_code == 0

    retrieve = runner.invoke(
        app,
        [
            "retrieve",
            "--agent",
            "support_agent",
            "--query",
            "refund status",
            "--session",
            "cli-s1",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert retrieve.exit_code == 0
    assert "episodic:cli-s1:evt-cli-1" in retrieve.output
    assert "score_breakdown" in retrieve.output

    project = runner.invoke(
        app,
        [
            "project",
            "--agent",
            "support_agent",
            "--query",
            "refund status",
            "--session",
            "cli-s1",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert project.exit_code == 0
    assert "projected_context" not in project.output
    assert "last_event_sequence" in project.output

    replay = runner.invoke(app, ["replay", "--data-dir", str(data_dir)])
    assert replay.exit_code == 0
    assert "state_hash" in replay.output

