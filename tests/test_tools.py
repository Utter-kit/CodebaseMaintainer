from pathlib import Path

from codebase_maintainer.tools import NoteTool, TerminalTool


def test_note_tool_create_list_search_summary(tmp_path):
    notes = NoteTool(tmp_path / "notes")
    note = notes.create(
        title="重构计划",
        content="服务层需要拆分并补充测试",
        note_type="action",
        tags=["flask"],
    )

    assert note["note_id"]
    assert notes.list(note_type="action")[0]["title"] == "重构计划"
    assert notes.search("服务层")[0]["note_id"] == note["note_id"]
    assert notes.summary()["by_type"]["action"] == 1


def test_terminal_tool_runs_in_workspace(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    terminal = TerminalTool(tmp_path)

    output = terminal.run({"command": "find . -name '*.py' | sort"})

    assert "./app.py" in output


def test_terminal_result_includes_failures(tmp_path):
    terminal = TerminalTool(Path(tmp_path))

    output = terminal.run({"command": "python3 -c 'import sys; sys.exit(3)'"})

    assert "[exit_code] 3" in output
