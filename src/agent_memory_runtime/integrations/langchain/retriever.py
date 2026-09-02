from __future__ import annotations

from typing import Any

from agent_memory_runtime.domain.query import MemoryQuery
from agent_memory_runtime.runtime import AgentMemoryRuntime

try:  # pragma: no cover - exercised only when langchain-core is installed
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
    from pydantic import Field
except ImportError:  # pragma: no cover - optional dependency
    Document = None  # type: ignore[assignment]
    BaseRetriever = object  # type: ignore[assignment,misc]
    Field = None  # type: ignore[assignment]


if Document is not None:

    class AgentMemoryLangChainRetriever(BaseRetriever):
        """Expose AgentMemoryRuntime retrieval as a LangChain retriever."""

        runtime: AgentMemoryRuntime = Field(exclude=True)
        agent_id: str
        tenant_id: str = "default"
        user_id: str | None = None
        session_id: str | None = None
        session_policy: str = "exact"
        limit: int | None = None

        def _get_relevant_documents(self, query: str, **_: Any) -> list[Document]:
            records, _trace = self.runtime.retrieve(
                MemoryQuery(
                    agent_id=self.agent_id,
                    text=query,
                    tenant_id=self.tenant_id,
                    user_id=self.user_id,
                    session_id=self.session_id,
                    session_policy=self.session_policy,
                    limit=self.limit,
                )
            )
            return [
                Document(
                    page_content=record.content,
                    metadata={
                        "memory_id": record.memory_id,
                        "tenant_id": record.tenant_id,
                        "user_id": record.user_id,
                        "agent_id": record.agent_id,
                        "session_id": record.session_id,
                        "level": record.level,
                        "memory_type": record.memory_type,
                        "visibility": record.visibility,
                        "status": record.status,
                        "tags": list(record.tags),
                    },
                )
                for record in records
            ]

else:

    class AgentMemoryLangChainRetriever:  # type: ignore[no-redef]
        def __init__(self, *_: Any, **__: Any) -> None:
            raise ImportError(
                "AgentMemoryLangChainRetriever requires the optional langchain extra: "
                'pip install "agent-memory-runtime[langchain]"'
            )
