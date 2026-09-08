from __future__ import annotations

import os
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000").rstrip("/")
SSE_GATEWAY_URL = os.getenv("SSE_GATEWAY_URL", "http://sse-gateway:8080").rstrip("/")
STATIC_ROOT = Path(os.getenv("STATIC_ROOT", "/app/dist")).resolve()
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class StaticProxyHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def do_GET(self) -> None:
        if self._is_proxy_path():
            self._proxy()
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self._is_proxy_path():
            self._proxy()
            return
        super().do_HEAD()

    def do_OPTIONS(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_PATCH(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def send_head(self):
        path = self.translate_path(self.path)
        if not Path(path).exists() and not self.path.startswith(("/api/", "/socket.io/")):
            self.path = "/index.html"
        return super().send_head()

    def _is_proxy_path(self) -> bool:
        return self.path.startswith(("/api/", "/socket.io/"))

    def _proxy(self) -> None:
        body = None
        if self.command not in {"GET", "HEAD"}:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        request = urllib.request.Request(
            self._upstream_url(),
            data=body,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if self._is_sse_response(response.headers):
                    self._copy_stream(response.status, response.headers, response)
                else:
                    self._copy_response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as error:
            self._copy_response(error.code, error.headers, error.read())
        except Exception as error:
            payload = str(error).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

    def _upstream_url(self) -> str:
        if self.path.startswith("/api/agent/events"):
            return SSE_GATEWAY_URL + self.path
        return BACKEND_URL + self.path

    @staticmethod
    def _is_sse_response(headers) -> bool:
        return str(headers.get("Content-Type") or "").lower().startswith("text/event-stream")

    def _copy_stream(self, status: int, headers, response) -> None:
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS or key.lower() == "content-length":
                continue
            self.send_header(key, value)
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            while True:
                line = response.readline()
                if not line:
                    return
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _copy_response(self, status: int, headers, body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), StaticProxyHandler)
    print(
        f"Serving {STATIC_ROOT} on :{port}, API={BACKEND_URL}, SSE={SSE_GATEWAY_URL}",
        flush=True,
    )
    server.serve_forever()
