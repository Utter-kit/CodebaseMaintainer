from datetime import datetime, timedelta

from codebase_maintainer.context import ContextBuilder, ContextConfig, ContextPacket


def test_context_builder_keeps_system_and_relevant_packets():
    builder = ContextBuilder(ContextConfig(max_tokens=250, min_relevance=0.0))
    context = builder.build(
        user_query="Flask API 测试",
        system_instructions="你是维护助手",
        custom_packets=[
            ContextPacket(
                content="Flask API routes need tests",
                timestamp=datetime.now() - timedelta(hours=1),
                metadata={"type": "code_analysis"},
            ),
            ContextPacket(
                content="Unrelated deployment note",
                timestamp=datetime.now() - timedelta(days=20),
                metadata={"type": "general"},
            ),
        ],
    )

    assert "[Role & Policies]" in context
    assert "Flask API routes need tests" in context
    assert "[Task]" in context


def test_context_builder_compresses_to_budget():
    builder = ContextBuilder(ContextConfig(max_tokens=80, min_relevance=0.0))
    long_packet = ContextPacket(
        content="重要上下文 " * 300,
        relevance_score=1.0,
        metadata={"type": "note"},
    )

    context = builder.build("重要上下文", custom_packets=[long_packet])

    assert "内容已压缩" in context
