"""Windows SCM host for the local BGE-M3 OpenAI-compatible embedding API."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import os
from pathlib import Path

import servicemanager
import uvicorn
import win32event
import win32service
import win32serviceutil


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    # pywin32 executes SvcDoRun on a worker thread; ProactorEventLoop tries to
    # install a process-wide wakeup fd and therefore cannot run there.
    return asyncio.SelectorEventLoop()


class RecommendRadioEmbeddingService(win32serviceutil.ServiceFramework):
    _svc_name_ = "RecommendRadioBgeM3"
    _svc_display_name_ = "Recommend Radio BGE-M3 Embedding"
    _svc_description_ = "Local BGE-M3 embedding API used by AMEM and Recommend Radio."

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server: uvicorn.Server | None = None

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.server is not None:
            self.server.should_exit = True
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self) -> None:
        servicemanager.LogInfoMsg("RecommendRadioBgeM3 starting")
        backend_root = Path(__file__).resolve().parent
        os.chdir(backend_root)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        config = uvicorn.Config(
            "embedding_server:app",
            host="127.0.0.1",
            port=8001,
            log_level=os.getenv("BGE_M3_LOG_LEVEL", "info"),
            log_config=None,
            access_log=False,
            loop="embedding_windows_service:selector_loop_factory",
        )
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None
        self.server.capture_signals = lambda: nullcontext()
        try:
            self.server.run()
        finally:
            servicemanager.LogInfoMsg("RecommendRadioBgeM3 stopped")


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(RecommendRadioEmbeddingService)
