from agent_memory_runtime.memory.intake.models import (
    AutoDreamReport,
    DreamCheckpoint,
    DreamProposal,
    MemoryAuditLog,
    MemoryProposal,
    MemoryProposalResult,
    MemoryToolIdentity,
    MemoryToolResult,
)

__all__ = [
    "AutoDreamAnalyzer",
    "AutoDreamReport",
    "DreamCheckpoint",
    "DreamProposal",
    "MemoryAuditLog",
    "MemoryProposal",
    "MemoryProposalResult",
    "MemoryIntakeError",
    "MemoryIntakeService",
    "MemoryToolIdentity",
    "MemoryToolResult",
    "build_memory_intake_tools",
]


def __getattr__(name: str) -> object:
    if name == "AutoDreamAnalyzer":
        from agent_memory_runtime.memory.intake.dream import AutoDreamAnalyzer

        return AutoDreamAnalyzer
    if name in {"MemoryIntakeError", "MemoryIntakeService"}:
        from agent_memory_runtime.memory.intake.service import (
            MemoryIntakeError,
            MemoryIntakeService,
        )

        return {
            "MemoryIntakeError": MemoryIntakeError,
            "MemoryIntakeService": MemoryIntakeService,
        }[name]
    if name == "build_memory_intake_tools":
        from agent_memory_runtime.memory.intake.tools import build_memory_intake_tools

        return build_memory_intake_tools
    raise AttributeError(name)
