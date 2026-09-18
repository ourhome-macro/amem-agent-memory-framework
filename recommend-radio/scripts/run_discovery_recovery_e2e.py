"""Verify the chat card completes without a reload when SSE events are missed."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    now = datetime.now(timezone.utc).isoformat()
    session_id = "agent-dialogue:recovery-e2e"
    task_id = "dialogue:recovery-e2e"
    job_id = "discovery:recovery-e2e"
    card_id = "agent-card:recovery-e2e"
    state = {"submitted": False, "refreshes": 0, "discoveryChecks": 0, "taskChecks": 0}

    def card(complete: bool) -> dict:
        return {
            "cardId": card_id,
            "kind": "recommendation_carousel",
            "status": "pending",
            "title": "唱跳舞台推荐",
            "prompt": "正在为你找唱跳舞台",
            "statement": "",
            "topic": "唱跳舞台",
            "polarity": "neutral",
            "sourceText": "给我推荐一些唱跳舞台",
            "createdAt": now,
            "updatedAt": now,
            "actions": [],
            "memoryIds": [],
            "discoveryJobId": job_id,
            "discoveryStatus": "completed" if complete else "running",
            "recommendations": [
                {
                    "track": {
                        "trackId": "bili:BV1234567890",
                        "bvid": "BV1234567890",
                        "cid": 1,
                        "title": "唱跳舞台 现场版",
                        "owner": "演出者",
                        "cover": "",
                        "duration": 180,
                    },
                    "source": "discovery_search",
                    "reason": "匹配本轮舞台请求",
                    "score": 10,
                }
            ] if complete else [],
            "tracks": [],
        }

    def session(complete: bool = False) -> dict:
        messages = [{
            "id": "1", "role": "assistant", "content": "你好，我在。", "createdAt": now,
        }]
        cards = []
        if state["submitted"]:
            current_card = card(complete)
            cards.append(current_card)
            messages.extend([
                {"id": "2", "role": "user", "content": "给我推荐一些唱跳舞台", "createdAt": now},
                {"id": "3", "role": "assistant", "content": "正在为你找唱跳舞台",
                 "cardId": card_id, "card": current_card, "createdAt": now},
            ])
        return {
            "sessionId": session_id, "state": "serving", "focus": "唱跳舞台",
            "createdAt": now, "updatedAt": now, "pendingContext": {},
            "messages": messages, "cards": cards,
            "analysis": {"scene": "conversation", "profile": {}, "summary": {}},
        }

    def ok(route, data: dict, status: int = 200) -> None:
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps({"success": True, "data": data}, ensure_ascii=False),
        )

    def handle_agent(route) -> None:
        request = route.request
        path = unquote(urlsplit(request.url).path)
        if path == "/api/agent/events":
            route.fulfill(status=200, headers={"Content-Type": "text/event-stream"},
                          body=": no events delivered\nretry: 1000\n\n")
        elif path == "/api/agent/dialogue" and request.method == "GET":
            ok(route, session())
        elif path == "/api/agent/dialogue/sessions" and request.method == "GET":
            ok(route, {"items": [{
                "sessionId": session_id, "title": "唱跳舞台", "preview": "",
                "state": "serving", "focus": "唱跳舞台",
                "createdAt": now, "updatedAt": now, "messageCount": 1,
            }]})
        elif path == "/api/agent/dialogue/tasks" and request.method == "POST":
            state["submitted"] = True
            ok(route, {"taskId": task_id, "sessionId": session_id, "status": "queued"}, 202)
        elif path.endswith("/tasks/" + task_id) and request.method == "GET":
            state["taskChecks"] += 1
            ok(route, {"taskId": task_id, "status": "completed", "result": session()})
        elif path.endswith("/cards/" + card_id + "/refresh") and request.method == "POST":
            state["refreshes"] += 1
            ok(route, session(complete=True))
        else:
            route.continue_()

    def handle_discovery(route) -> None:
        state["discoveryChecks"] += 1
        completed = state["discoveryChecks"] >= 2
        ok(route, {"jobId": job_id, "available": True,
                   "status": "completed" if completed else "running"})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/settings/deepseek-key", lambda route: ok(route, {
            "configured": True, "fallbackConfigured": False, "provider": "deepseek",
        }))
        page.route("**/api/agent/**", handle_agent)
        page.route("**/api/recommendations/discovery/*", handle_discovery)
        page.goto(args.base_url.rstrip("/") + "/#/agent", wait_until="domcontentloaded")
        editor = page.locator(".composer textarea")
        expect(editor).to_be_enabled(timeout=30000)
        editor.fill("给我推荐一些唱跳舞台")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator(".song-glass-card")).to_have_count(1, timeout=20000)
        assert page.locator(".song-glass-card").inner_text().find("唱跳舞台") >= 0
        assert state["taskChecks"] >= 1 and state["discoveryChecks"] >= 2
        assert state["refreshes"] == 1 and not errors, errors
        print(json.dumps({"passed": True, **state, "javascriptErrors": errors}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
