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
