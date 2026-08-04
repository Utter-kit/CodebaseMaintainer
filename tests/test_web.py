from codebase_maintainer.web import WebState, render_page


def test_render_page_contains_main_workflows(tmp_path):
    codebase = tmp_path / "app"
    codebase.mkdir()
    (codebase / "api.py").write_text("# TODO add tests\n", encoding="utf-8")
    state = WebState("demo", str(codebase), str(tmp_path / "notes"))

    html = render_page(state)

    assert "CodebaseMaintainer" in html
    assert "运行助手" in html
    assert "终端探索" in html
    assert "记录笔记" in html
    assert "api.py" in html


def test_render_page_supports_independent_workflow_pages(tmp_path):
    codebase = tmp_path / "app"
    codebase.mkdir()
    state = WebState("demo", str(codebase), str(tmp_path / "notes"))

    pages = {
        "assistant": "初始化助手",
        "explore": "第一天: 探索代码库",
        "analyze": "第二天: 分析代码质量",
        "plan": "第三天: 规划重构任务",
    }
    for view, title in pages.items():
        html = render_page(state, view)
        assert title in html
        assert "workflow-button active" in html
        assert f'value="/{view}"' in html

    assert 'href="/explore"' in render_page(state, "assistant")
