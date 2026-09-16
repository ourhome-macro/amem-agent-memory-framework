"""Exercise the real local-account stack and produce a credential-free trace report."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
import threading
import time
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:23000")
    args = parser.parse_args()
    session = requests.Session()
    trace = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    session.headers["traceparent"] = f"00-{trace}-{span_id}-01"
    checks = []
    report = {"traceId": trace, "checks": checks, "liveAccount": True}

    def query(database, sql, parameters=()):
        code = (
            "import sqlite3,json,sys; "
            "c=sqlite3.connect('file:/app/data/'+sys.argv[1]+'?mode=ro',uri=True); "
            "print(json.dumps(c.execute(sys.argv[2],json.loads(sys.argv[3])).fetchall())); "
            "c.close()"
        )
        command = [
            "docker",
            "compose",
            "--env-file",
            str(args.run_dir / "private.env"),
            "-f",
            str(Path(__file__).resolve().parents[1] / "docker-compose.e2e.yml"),
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            code,
            database,
            sql,
            json.dumps(parameters),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        if completed.returncode:
            raise RuntimeError("Container database verification failed")
        return json.loads(completed.stdout)

    def request(method, path, body=None, **kwargs):
        started = time.monotonic()
        response = session.request(method, args.url + path, json=body, timeout=180, **kwargs)
        response.raise_for_status()
        checks.append(
            {
                "name": path,
                "status": response.status_code,
                "durationMs": round((time.monotonic() - started) * 1000, 2),
                "serverTraceId": response.headers.get("X-Trace-ID", ""),
            }
        )
        value = response.json()
        return value.get("data", value)

    try:
        response = session.get(args.url, timeout=10)
        assert response.status_code == 200 and '<div id="app">' in response.text
        checks.append({"name": "frontend_bundle", "passed": True})
        identity = request("GET", "/api/session/me")
        assert identity["authenticated"]
        auth = request("POST", "/api/auth/status/refresh", {})
        assert auth["isLoggedIn"], "Local Bilibili session is not authenticated"
        report["bilibiliAuthenticated"] = True
        dialogue = request("POST", "/api/agent/dialogue/sessions", {})
        session_id = dialogue["sessionId"]
        events = []
        stream_errors = []
        stop = threading.Event()

        def stream():
            try:
                with requests.get(
                    args.url + "/api/agent/events",
                    params={"sessionId": session_id},
                    stream=True,
                    timeout=(10, 60),
                ) as incoming:
                    incoming.raise_for_status()
                    for line in incoming.iter_lines(chunk_size=1, decode_unicode=True):
                        if stop.is_set():
                            break
                        if line.startswith("data:"):
                            events.append(json.loads(line[5:].strip()))
            except Exception as error:
                if not stop.is_set():
                    stream_errors.append(type(error).__name__)

        thread = threading.Thread(target=stream, daemon=True)
        thread.start()
        time.sleep(0.5)
        task_body = {
            "sessionId": session_id,
            "message": "按我已有的喜好推荐几首适合放松的歌，避免Rap和说唱。",
        }
        key = "framework-e2e-" + secrets.token_hex(8)
        accepted = request(
            "POST", "/api/agent/dialogue/tasks", task_body, headers={"Idempotency-Key": key}
        )
        duplicate = request(
            "POST", "/api/agent/dialogue/tasks", task_body, headers={"Idempotency-Key": key}
        )
        assert duplicate["taskId"] == accepted["taskId"]
        report["taskId"] = accepted["taskId"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            response = session.get(
                args.url + "/api/agent/dialogue/tasks/" + accepted["taskId"], timeout=10
            )
            response.raise_for_status()
            status = response.json()["data"]
            if status["status"] in {"completed", "failed", "needs_reconciliation"}:
                break
            time.sleep(1)
        assert status["status"] == "completed", f"Task ended in {status['status']}"
        checks.append({"name": "dialogue_via_langgraph_celery", "passed": True})
        discovery = request(
            "POST",
            "/api/recommendations/discovery",
            {
                "scene": "conversation",
                "limit": 3,
                "requestText": "R&B 音乐",
            },
        )
        report["discoveryJobId"] = discovery["jobId"]
        assert discovery["jobId"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            response = session.get(
                args.url + "/api/recommendations/discovery/" + discovery["jobId"], timeout=15
            )
            response.raise_for_status()
            discovery_status = response.json()["data"]
            if discovery_status["status"] in {"completed", "failed", "needs_reconciliation"}:
                break
            time.sleep(1)
        assert discovery_status["status"] == "completed", discovery_status["status"]
        discovery_run = query('bili_radio.sqlite3',
            'SELECT agent_run_id FROM durable_jobs WHERE job_id=?', (discovery['jobId'],))[0][0]
        stage_rows = query('agent-runs.sqlite3',
            "SELECT json_extract(payload,'$.tool_name'),status FROM agent_tool_calls WHERE run_id=?",
            (discovery_run,))
        report['discoveryStages'] = dict(stage_rows)
        assert report['discoveryStages'] == {
            'radio_discovery_' + name: 'succeeded' for name in ('prepare','search','admit','embed')
        }, report['discoveryStages']
        report["discoveryResult"] = {
            k: v
            for k, v in discovery_status.get("result", {}).items()
            if k in {"enqueued", "admitted", "queryCount", "embedding"}
        }
        checks.append({"name": "discovery_via_langgraph_celery", "passed": True})
        recommendation = request("GET", "/api/recommendations?scene=home&limit=3")
        items = recommendation.get("items") or []
        assert items, "No real candidate was recommended"
        track = items[0]["track"]
        report["recommendationCount"] = len(items)
        report["trackId"] = track["trackId"]
        request(
            "POST",
            "/api/recommendations/events",
            {
                "trackId": track["trackId"],
                "event": "played",
                "scene": "home",
                "playedSeconds": 30,
                "eventId": "e2e-" + trace,
                "recommendationTraceId": recommendation.get("traceId")
                or recommendation.get("debugTraceId"),
            },
        )
        feedback_id = (
            "behavior:"
            + hashlib.sha256(
                json.dumps(
                    [identity["user"]["id"], "played", "home", "e2e-" + trace], ensure_ascii=False
                ).encode()
            ).hexdigest()
        )
        # Request stream metadata and read a bounded chunk of actual media.
        bvid = track["bvid"]
        media_path = "/api/tracks/" + bvid + (("/" + str(track["cid"])) if track.get("cid") else "")
        request("GET", media_path + "/stream-info")
        with session.get(
            args.url + media_path + "/stream",
            headers={"Range": "bytes=0-4095"},
            stream=True,
            timeout=(15, 30),
        ) as media:
            media.raise_for_status()
            chunk = next(media.iter_content(chunk_size=4096))
            assert chunk
            report["mediaBytesVerified"] = len(chunk)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not events:
            time.sleep(0.5)
        stop.set()
        report["sseEventCount"] = len(events)
        assert events and not stream_errors, f"SSE errors: {stream_errors}"
        checks.append({"name": "sse_delivery", "passed": True})
        job = query(
            "bili_radio.sqlite3",
            "SELECT agent_run_id FROM durable_jobs WHERE job_id=?",
            (accepted["taskId"],),
        )[0]
        assert job and job[0]
        report["agentRunId"] = job[0]
        assert (
            query("bili_radio.sqlite3", "SELECT version_num FROM alembic_version")[0][0]
            == "radio_002"
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            rows = query(
                "bili_radio.sqlite3",
                "SELECT status FROM durable_jobs WHERE job_id=?",
                (feedback_id,),
            )
            feedback = rows[0] if rows else None
            if feedback and feedback[0] == "completed":
                break
            time.sleep(0.5)
        assert feedback and feedback[0] == "completed"
        assert (
            query("amem.sqlite3", "SELECT COUNT(*) FROM events WHERE event_id=?", (feedback_id,))[
                0
            ][0]
            == 1
        )
        checks.append({"name": "feedback_committed_to_amem", "passed": True})
        report["passed"] = True
    except Exception as error:
        report["passed"] = False
        report["errorType"] = type(error).__name__
        report["error"] = str(error)[:300]
    finally:
        session.close()
    time.sleep(3)
    spans = []
    path = args.run_dir / "traces" / "traces.json"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                batch = json.loads(line)
            except json.JSONDecodeError:
                continue
            for resource in batch.get("resourceSpans", []):
                attributes = resource.get("resource", {}).get("attributes", [])
                service = next(
                    (
                        a["value"].get("stringValue", "")
                        for a in attributes
                        if a["key"] == "service.name"
                    ),
                    "",
                )
                for scope in resource.get("scopeSpans", []):
                    for item in scope.get("spans", []):
                        if item.get("traceId") == trace:
                            spans.append(
                                {
                                    "name": item["name"],
                                    "service": service,
                                    "spanId": item["spanId"],
                                    "parentSpanId": item.get("parentSpanId", ""),
                                    "durationMs": round(
                                        (
                                            int(item["endTimeUnixNano"])
                                            - int(item["startTimeUnixNano"])
                                        )
                                        / 1e6,
                                        2,
                                    ),
                                    "status": item.get("status", {}),
                                }
                            )
    report["spans"] = spans
    report["services"] = sorted({item["service"] for item in spans})
    required = {"recommend-radio-api", "radio-worker", "radio-outbox", "amem-grpc", "radio-sse"}
    report["traceComplete"] = required <= set(report["services"])
    report["discoveryTraced"] = any(item["name"] == "music.discovery" for item in spans)
    required_stages = {'music.stage.' + name for name in ('prepare','search','admit','embed')}
    report['workflowStagesTraced'] = required_stages <= {item['name'] for item in spans}
    report["llmSuccessCount"] = sum(
        item["name"].startswith("llm.")
        and item["status"].get("code", 0) not in (2, "STATUS_CODE_ERROR")
        for item in spans
    )
    report["llmErrorCount"] = sum(
        item["name"].startswith("llm.")
        and item["status"].get("code", 0) in (2, "STATUS_CODE_ERROR")
        for item in spans
    )
    report['errorSpanCount'] = sum(item['status'].get('code',0) in (2,'STATUS_CODE_ERROR')
                                   for item in spans)
    report["passed"] = (
        report["passed"]
        and report["traceComplete"]
        and report["llmSuccessCount"] > 0
        and report["discoveryTraced"]
        and report['workflowStagesTraced']
        and report['errorSpanCount']==0
    )
    result_path = args.run_dir / "result.json"
    result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"spans", "checks"}}, ensure_ascii=False
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
