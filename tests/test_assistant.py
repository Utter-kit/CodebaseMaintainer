from codebase_maintainer.assistant import CodebaseMaintainer


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "已完成分析。下一步为任务拆分和测试补齐。"


def test_assistant_run_collects_context_and_updates_history(tmp_path):
    codebase = tmp_path / "app"
    codebase.mkdir()
    (codebase / "api.py").write_text("# TODO add tests\n", encoding="utf-8")
    llm = FakeLLM()
    assistant = CodebaseMaintainer(
        project_name="demo",
        codebase_path=str(codebase),
        notes_path=str(tmp_path / "notes"),
        llm=llm,
    )

    response = assistant.run("请分析 API", mode="auto")

    assert "已完成分析" in response
    assert len(assistant.conversation_history) == 2
    assert "api.py" in llm.prompts[0]
    assert assistant.get_stats()["activity"]["commands_executed"] == 1


def test_assistant_plan_creates_action_note(tmp_path):
    codebase = tmp_path / "app"
    codebase.mkdir()
    llm = FakeLLM()
    assistant = CodebaseMaintainer(
        project_name="demo",
        codebase_path=str(codebase),
        notes_path=str(tmp_path / "notes"),
        llm=llm,
    )

    assistant.plan_next_steps()

    assert assistant.note_tool.summary()["by_type"]["action"] == 1
