"""Drive the real Vue UI in a fresh headless browser context."""

import argparse
import json
import secrets
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:23000")
    args = parser.parse_args()
    trace_id = secrets.token_hex(16)
    errors = []
    report = {"traceId": trace_id, "passed": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 960},
            extra_http_headers={"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"},
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)[:160]))
        try:
            page.goto(
                f"{args.base_url.rstrip('/')}/#/agent", wait_until="domcontentloaded", timeout=60000
            )
            expect(page.locator(".agent-heading h1")).to_be_visible(timeout=30000)
            new_chat = page.get_by_role("button", name="新对话", exact=True)
            expect(new_chat).to_be_enabled(timeout=60000)
            with page.expect_response(
                lambda response: response.url.endswith("/api/agent/dialogue/sessions")
                and response.request.method == "POST",
                timeout=30000,
            ) as created:
                new_chat.click()
            new_session_id = created.value.json()["data"]["sessionId"]
            expect(page.locator(".chat-surface")).to_have_attribute(
                "data-session-id", new_session_id, timeout=30000
            )
            editor = page.locator("textarea")
            expect(editor).to_be_enabled(timeout=60000)
            before = page.locator(".message-row.assistant:not(.thinking-row)").count()
            editor.fill("你好")
            page.get_by_role("button", name="发送", exact=True).click()
            expect(page.locator(".message-row.user").last).to_contain_text("你好", timeout=30000)
            page.wait_for_function(
                '(count) => document.querySelectorAll(".message-row.assistant:not(.thinking-row)").length > count',
                arg=before,
                timeout=120000,
            )
            expect(page.locator(".thinking-row")).to_have_count(0, timeout=120000)
            expect(page.locator(".error-text")).to_have_count(0)
            reply = page.locator(".message-row.assistant:not(.thinking-row)").last
            reply.scroll_into_view_if_needed()
            expect(reply).to_be_in_viewport()
            page.screenshot(path=str(args.run_dir / "browser-dialogue.png"), full_page=True)
            assistant_count = page.locator(".message-row.assistant:not(.thinking-row)").count()
            page.once("dialog", lambda dialog: dialog.accept())
            with page.expect_response(
                lambda response: response.request.method == "DELETE"
                and "/api/agent/dialogue/sessions/" in response.url,
                timeout=30000,
            ) as deleted:
                page.locator(".context-row:has(.context-item.active) .delete-session-btn").click()
            assert deleted.value.status == 200
            report.update(
                passed=not errors,
                assistantMessages=assistant_count,
                sessionId=new_session_id,
                javascriptErrors=errors,
                steps=["load_vue", "new_session", "send_message", "receive_sse_reply", "delete_session"],
            )
        except Exception as error:
            report.update(
                errorType=type(error).__name__, error=str(error)[:400], javascriptErrors=errors
            )
            page.screenshot(path=str(args.run_dir / "browser-failure.png"), full_page=True)
        finally:
            context.close()
            browser.close()
    (args.run_dir / "browser-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
