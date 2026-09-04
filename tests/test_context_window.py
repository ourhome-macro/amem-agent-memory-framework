from __future__ import annotations

from agent_memory_runtime.agent.context_window import compact_checkpoint
from agent_memory_runtime.agent.models import AgentCheckpoint, ModelMessage
from agent_memory_runtime.agent.policy import AgentPolicy
from agent_memory_runtime.tokens import AdaptiveTokenEstimator


def test_compacts_42_message_history_into_structured_context() -> None:
    estimator = AdaptiveTokenEstimator()
    messages = _long_history_messages()
    checkpoint = AgentCheckpoint(run_id="compact-run", messages=messages)
    policy = AgentPolicy(
        model_context_tokens=2_200,
        reserved_output_tokens=400,
        context_compaction_ratio=0.5,
        context_keep_recent_messages=6,
        context_summary_max_tokens=900,
    )

    compacted, report = compact_checkpoint(
        checkpoint,
        tools=(),
        estimator=estimator,
        policy=policy,
        model=None,
    )

    assert report is not None
    assert len(messages) == 42
    assert 5_000 <= report.before_tokens <= 6_500
    assert len(compacted.messages) <= 10
    assert report.after_tokens <= 1_800
    assert report.after_tokens < report.before_tokens * 0.4
    assert compacted.messages[0].role == "system"
    assert compacted.messages[1].content.startswith("<pinned-facts>")
    assert compacted.messages[2].role == "system"
    assert compacted.messages[2].content.startswith("<task-state>")
    assert "initial_task_context:" in compacted.messages[2].content
    assert "current_user_intent:" in compacted.messages[2].content
    assert "relation_to_initial_task:" in compacted.messages[2].content
    assert messages[1].content in compacted.messages[2].content
    assert compacted.messages[3].content.startswith("<compacted-conversation-summary>")
    assert "核心诉求:" in compacted.messages[3].content
    assert "已完成操作:" in compacted.messages[3].content
    assert "达成共识:" in compacted.messages[3].content
    assert "未解决待办:" in compacted.messages[3].content
    assert compacted.messages[-6:] == messages[-6:]


def _long_history_messages() -> tuple[ModelMessage, ...]:
    body = (
        "Context compression benchmark sentence with repository details, tool calls, "
        "memory runtime constraints, audit evidence, and production safety notes. "
    )
    messages: list[ModelMessage] = [
        ModelMessage(role="system", content="System rules stay exact and uncompressed."),
        ModelMessage(
            role="user",
            content="Build a production-grade agent memory query tool and compression plan.",
        ),
    ]
    for index in range(20):
        user_content = f"Round {index} user asks for detail. {body * 2}"
        assistant_content = f"Round {index} assistant reports completed work. {body * 2}"
        if index == 4:
            user_content = (
                "用户明确要求：工具查询记忆必须继承 AgentRequest 的 tenant/user 身份边界。"
                f" {body * 2}"
            )
        if index == 8:
            assistant_content = (
                "已确认方案：memory.search 必须调用 AgentMemoryRuntime.project()，"
                f"不能直接读取 MemoryStore。 {body * 2}"
            )
        messages.append(ModelMessage(role="user", content=user_content))
        messages.append(ModelMessage(role="assistant", content=assistant_content))
    return tuple(messages)
