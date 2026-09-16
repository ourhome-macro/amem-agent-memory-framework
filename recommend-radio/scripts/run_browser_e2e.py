"""Drive the real Vue UI in a fresh headless browser context."""

import argparse
import json
import secrets
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
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
                "http://127.0.0.1:23000/#/agent", wait_until="domcontentloaded", timeout=60000
            )
            expect(page.get_by_role("heading", name="音乐搭子")).to_be_visible(timeout=30000)
            new_chat = page.get_by_role("button", name="新聊天", exact=True)
            expect(new_chat).to_be_enabled(timeout=60000)
            new_chat.click()
            editor = page.locator("textarea")
            expect(editor).to_be_enabled(timeout=60000)
            before = page.locator(".message-row.assistant").count()
            editor.fill("你好")
            page.get_by_role("button", name="发送", exact=True).click()
            expect(page.locator(".message-row.user").last).to_contain_text("你好", timeout=30000)
            page.wait_for_function(
                '(count) => document.querySelectorAll(".message-row.assistant").length > count',
                arg=before,
                timeout=120000,
            )
            expect(page.locator(".error-text")).to_have_count(0)
            reply = page.locator(".message-row.assistant").last
            reply.scroll_into_view_if_needed()
            expect(reply).to_be_in_viewport()
            page.screenshot(path=str(args.run_dir / "browser-dialogue.png"), full_page=True)
            report.update(
                passed=not errors,
                assistantMessages=page.locator(".message-row.assistant").count(),
                javascriptErrors=errors,
                steps=["load_vue", "new_session", "send_message", "receive_sse_reply"],
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
