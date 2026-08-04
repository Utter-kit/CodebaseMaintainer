from __future__ import annotations

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .assistant import CodebaseMaintainer


DEFAULT_QUERY = "请探索这个代码库并给出维护建议。"


VIEW_CONFIG = {
    "assistant": {
        "path": "/assistant",
        "label": "运行助手",
        "mode": "auto",
        "eyebrow": "初始化助手",
        "title": "运行助手",
        "query": DEFAULT_QUERY,
        "hint": "对应 maintainer.run(...)，适合临时提问、指定文件分析和连续对话。",
    },
    "explore": {
        "path": "/explore",
        "label": "探索结构",
        "mode": "explore",
        "eyebrow": "第一天: 探索代码库",
        "title": "探索结构",
        "query": "请探索这个代码库的目录结构、核心模块和下一步维护入口。",
        "hint": "对应 maintainer.explore()，会优先扫描 Python 文件结构并给出项目理解。",
    },
    "analyze": {
        "path": "/analyze",
        "label": "分析质量",
        "mode": "analyze",
        "eyebrow": "第二天: 分析代码质量",
        "title": "分析质量",
        "query": "请分析代码质量，重点关注重复代码、复杂度、TODO/FIXME 和缺失测试。",
        "hint": "对应 maintainer.analyze()，会收集行数、TODO/FIXME 和质量风险。",
    },
    "plan": {
        "path": "/plan",
        "label": "规划任务",
        "mode": "plan",
        "eyebrow": "第三天: 规划重构任务",
        "title": "规划任务",
        "query": "请基于已有笔记和当前进度，整理下一阶段重构任务优先级。",
        "hint": "对应 maintainer.plan_next_steps()，会回顾 task_state、blocker 和 action 笔记。",
    },
}

PATH_TO_VIEW = {config["path"]: name for name, config in VIEW_CONFIG.items()}
PATH_TO_VIEW["/"] = "assistant"


class WebState:
    def __init__(
        self,
        project_name: str,
        codebase_path: str,
        notes_path: str | None,
    ) -> None:
        self.project_name = project_name
        self.codebase_path = str(Path(codebase_path).expanduser().resolve())
        self.notes_path = notes_path
        self.assistant = self._build_assistant()
        self.last_response = ""
        self.last_command_output = ""
        self.last_error = ""

    def configure(
        self,
        project_name: str,
        codebase_path: str,
        notes_path: str | None,
    ) -> None:
        next_codebase = str(Path(codebase_path).expanduser().resolve())
        next_notes = notes_path or None
        changed = (
            project_name != self.project_name
            or next_codebase != self.codebase_path
            or next_notes != self.notes_path
        )
        self.project_name = project_name
        self.codebase_path = next_codebase
        self.notes_path = next_notes
        if changed:
            self.assistant = self._build_assistant()
            self.last_response = ""
            self.last_command_output = ""

    def _build_assistant(self) -> CodebaseMaintainer:
        return CodebaseMaintainer(
            project_name=self.project_name,
            codebase_path=self.codebase_path,
            notes_path=self.notes_path,
        )


class MaintainerHandler(BaseHTTPRequestHandler):
    server_version = "CodebaseMaintainerUI/0.1"

    @property
    def state(self) -> WebState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        view = PATH_TO_VIEW.get(parsed.path)
        if not view:
            self._send_html(render_not_found(), status=404)
            return
        self._send_html(render_page(self.state, view))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        fields = self._read_form()
        return_to = safe_return_path(first(fields, "return_to", "/assistant"))
        try:
            if parsed.path == "/run":
                self._configure_from_form(fields)
                view = PATH_TO_VIEW.get(return_to, "assistant")
                query = first(fields, "query", VIEW_CONFIG[view]["query"])
                mode = first(fields, "mode", VIEW_CONFIG[view]["mode"])
                self.state.last_response = self.state.assistant.run(query, mode=mode)
                self.state.last_error = ""
            elif parsed.path == "/command":
                self._configure_from_form(fields)
                command = first(fields, "command", "find . -maxdepth 2 -type f | sort")
                self.state.last_command_output = self.state.assistant.execute_command(command)
                self.state.last_error = ""
            elif parsed.path == "/note":
                self._configure_from_form(fields)
                self.state.assistant.create_note(
                    title=first(fields, "note_title", "手动记录"),
                    content=first(fields, "note_content", ""),
                    note_type=first(fields, "note_type", "general"),
                    tags=[self.state.project_name, "manual"],
                )
                self.state.last_error = ""
            else:
                self._send_html(render_not_found(), status=404)
                return
        except Exception as exc:
            self.state.last_error = str(exc)

        self._redirect(return_to)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {format % args}")

    def _configure_from_form(self, fields: dict[str, list[str]]) -> None:
        self.state.configure(
            project_name=first(fields, "project_name", self.state.project_name),
            codebase_path=first(fields, "codebase_path", self.state.codebase_path),
            notes_path=first(fields, "notes_path", self.state.notes_path or ""),
        )

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return parse_qs(raw, keep_blank_values=True)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _send_html(self, html: str, status: int = 200) -> None:
        encoded = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def run_server(
    host: str,
    port: int,
    project_name: str,
    codebase_path: str,
    notes_path: str | None,
) -> None:
    state = WebState(project_name, codebase_path, notes_path)
    server = ThreadingHTTPServer((host, port), MaintainerHandler)
    server.state = state  # type: ignore[attr-defined]
    print(f"CodebaseMaintainer UI running at http://{host}:{port}")
    print(f"Codebase path: {state.codebase_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down CodebaseMaintainer UI")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the CodebaseMaintainer web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project-name", default="my_flask_app")
    parser.add_argument("--codebase-path", default=".")
    parser.add_argument("--notes-path", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_server(
        host=args.host,
        port=args.port,
        project_name=args.project_name,
        codebase_path=args.codebase_path,
        notes_path=args.notes_path,
    )
    return 0


def render_page(state: WebState, active_view: str = "assistant") -> str:
    stats = state.assistant.get_stats()
    notes = state.assistant.note_tool.list(limit=8)
    files_output = safe_command(
        state.assistant,
        "find . -path ./.git -prune -o -path '*/__pycache__' -prune -o -maxdepth 3 -type f -print | sort | head -n 80",
    )
    view = VIEW_CONFIG.get(active_view, VIEW_CONFIG["assistant"])
    mode_options = options(
        ["auto", "explore", "analyze", "plan"],
        view["mode"],
    )
    note_type_options = options(
        ["general", "task_state", "blocker", "action", "conclusion", "finding"],
        "finding",
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CodebaseMaintainer UI</title>
  <style>{CSS}</style>
</head>
<body>
  <canvas class="ambient-canvas" id="ambient-canvas" aria-hidden="true"></canvas>
  <div class="background-scan" aria-hidden="true"></div>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">Long-running Agent Console</p>
        <h1>CodebaseMaintainer</h1>
      </div>
      <div class="status-pill">
        <span></span>
        本地运行
      </div>
    </header>

    {render_error(state.last_error)}

    {render_workflow_header(active_view)}

    <section class="hero">
      <form class="panel control-panel" method="post" action="/run">
        <input type="hidden" name="return_to" value="{escape(view['path'])}">
        <div class="form-grid">
          <label>
            <span>项目名</span>
            <input name="project_name" value="{escape(state.project_name)}">
          </label>
          <label>
            <span>代码库路径</span>
            <input name="codebase_path" value="{escape(state.codebase_path)}">
          </label>
          <label>
            <span>笔记目录</span>
            <input name="notes_path" value="{escape(state.notes_path or '')}" placeholder="./my_flask_app_notes">
          </label>
          <label>
            <span>运行模式</span>
            <select name="mode">{mode_options}</select>
          </label>
        </div>
        <label>
          <span>{escape(view["title"])}任务</span>
          <textarea name="query" rows="4">{escape(view["query"])}</textarea>
        </label>
        {render_workflow_nav(active_view)}
        <div class="run-row">
          <button type="submit">执行{escape(view["label"])}</button>
          <span>{escape(view["hint"])}</span>
        </div>
      </form>

      <aside class="panel metrics">
        <div>
          <span class="metric-label">Commands</span>
          <strong>{stats["activity"]["commands_executed"]}</strong>
        </div>
        <div>
          <span class="metric-label">Notes</span>
          <strong>{stats["notes"]["total"]}</strong>
        </div>
        <div>
          <span class="metric-label">Issues</span>
          <strong>{stats["activity"]["issues_found"]}</strong>
        </div>
        <div class="session">
          <span>Session</span>
          <code>{escape(stats["session_info"]["session_id"])}</code>
        </div>
        <div class="context-visual" aria-hidden="true">
          <i style="--i: 0"></i>
          <i style="--i: 1"></i>
          <i style="--i: 2"></i>
          <i style="--i: 3"></i>
          <i style="--i: 4"></i>
          <i style="--i: 5"></i>
        </div>
      </aside>
    </section>

    {render_view_stage(active_view, state, stats, notes, files_output)}

    <section class="workspace-grid">
      <form class="panel" method="post" action="/command">
        {hidden_config_fields(state, view["path"])}
        <div class="panel-heading">
          <h2>终端探索</h2>
          <span>在目标代码库目录内执行</span>
        </div>
        <label>
          <span>命令</span>
          <input name="command" value="grep -RIn 'TODO\\|FIXME' --include='*.py' . | head -n 20">
        </label>
        <button type="submit">执行命令</button>
        <pre>{escape(state.last_command_output or "命令输出会显示在这里。")}</pre>
      </form>

      <form class="panel" method="post" action="/note">
        {hidden_config_fields(state, view["path"])}
        <div class="panel-heading">
          <h2>记录笔记</h2>
          <span>跨会话保存到 JSON</span>
        </div>
        <label>
          <span>标题</span>
          <input name="note_title" value="发现：">
        </label>
        <label>
          <span>类型</span>
          <select name="note_type">{note_type_options}</select>
        </label>
        <label>
          <span>内容</span>
          <textarea name="note_content" rows="5"></textarea>
        </label>
        <button type="submit">保存笔记</button>
      </form>
    </section>

    <section class="panel notes">
      <div class="panel-heading">
        <h2>最近笔记</h2>
        <span>{escape(str(stats["notes"]["by_type"]))}</span>
      </div>
      <div class="note-list">
        {render_notes(notes)}
      </div>
    </section>
  </main>
<script>{SCRIPT}</script>
</body>
</html>"""


def render_view_stage(
    active_view: str,
    state: WebState,
    stats: dict[str, Any],
    notes: list[dict[str, Any]],
    files_output: str,
) -> str:
    output = escape(state.last_response or VIEW_CONFIG[active_view]["hint"])
    if active_view == "explore":
        return f'''<section class="view-stage explore-stage">
  <article class="panel output stage-primary">
    <div class="panel-heading">
      <h2>代码地图</h2>
      <span>目录结构和入口文件</span>
    </div>
    <div class="map-strip" aria-hidden="true"><b>app</b><b>models</b><b>routes</b><b>services</b><b>tests</b></div>
    <pre>{escape(files_output)}</pre>
  </article>
  <aside class="panel mode-card">
    <div class="page-sigil">EXPLORE</div>
    <h2>探索步骤</h2>
    <ol class="flow-list">
      <li><strong>扫描文件树</strong><span>定位 Flask app、tests、migrations 和配置入口。</span></li>
      <li><strong>识别模块边界</strong><span>把 models、routes、services、utils 组织成维护地图。</span></li>
      <li><strong>记录发现</strong><span>把架构结论保存为 finding 或 conclusion 笔记。</span></li>
    </ol>
  </aside>
</section>'''
    if active_view == "analyze":
        return f'''<section class="view-stage analyze-stage">
  <article class="panel output stage-primary">
    <div class="panel-heading">
      <h2>质量报告</h2>
      <span>重复、复杂度、TODO 和测试风险</span>
    </div>
    <pre>{output}</pre>
  </article>
  <aside class="panel mode-card risk-panel">
    <div class="page-sigil">ANALYZE</div>
    <h2>风险矩阵</h2>
    <div class="risk-grid">
      <div><strong>HIGH</strong><span>重复服务逻辑</span></div>
      <div><strong>MED</strong><span>复杂函数嵌套</span></div>
      <div><strong>TEST</strong><span>覆盖率缺口</span></div>
      <div><strong>TODO</strong><span>注释债务清理</span></div>
    </div>
  </aside>
</section>'''
    if active_view == "plan":
        return f'''<section class="view-stage plan-stage">
  <article class="panel output stage-primary">
    <div class="panel-heading">
      <h2>重构看板</h2>
      <span>按优先级推进长期任务</span>
    </div>
    <div class="task-board">
      <section><h3>高优先级</h3><p>User.email 唯一约束</p><p>提取 BaseService</p><p>重构 process_order</p></section>
      <section><h3>中优先级</h3><p>services 单元测试</p><p>清理 TODO 注释</p><p>Order 时间字段</p></section>
      <section><h3>低优先级</h3><p>性能优化</p><p>文档更新</p><p>报告归档</p></section>
    </div>
  </article>
  <aside class="panel mode-card">
    <div class="page-sigil">PLAN</div>
    <h2>当前记忆</h2>
    <p class="mode-summary">已记录 {stats["notes"]["total"]} 条笔记，最近任务会进入下一轮 ContextBuilder。</p>
    <div class="mini-notes">{render_compact_notes(notes)}</div>
  </aside>
</section>'''
    return f'''<section class="view-stage assistant-stage">
  <article class="panel output stage-primary">
    <div class="panel-heading">
      <h2>对话控制台</h2>
      <span>ContextBuilder + NoteTool + TerminalTool</span>
    </div>
    <pre>{output}</pre>
  </article>
  <aside class="panel mode-card">
    <div class="page-sigil">RUN</div>
    <h2>运行模式</h2>
    <ol class="flow-list">
      <li><strong>输入问题</strong><span>可以指定文件、目录、错误或重构目标。</span></li>
      <li><strong>构建上下文</strong><span>自动合并最近对话、笔记和终端探索结果。</span></li>
      <li><strong>沉淀状态</strong><span>问题、计划和结论会进入长期笔记。</span></li>
    </ol>
  </aside>
</section>'''


def render_compact_notes(notes: list[dict[str, Any]]) -> str:
    if not notes:
        return '<p>暂无任务笔记。</p>'
    items = []
    for note in notes[:4]:
        items.append(
            f'<p><strong>{escape(note.get("type", "general"))}</strong><span>{escape(note.get("title", "Untitled"))}</span></p>'
        )
    return "".join(items)


def safe_command(assistant: CodebaseMaintainer, command: str) -> str:
    try:
        return assistant.terminal_tool.run({"command": command})
    except Exception as exc:
        return f"无法读取代码库快照: {exc}"


def hidden_config_fields(state: WebState, return_to: str = "/assistant") -> str:
    return (
        f'<input type="hidden" name="return_to" value="{escape(return_to)}">'
        f'<input type="hidden" name="project_name" value="{escape(state.project_name)}">'
        f'<input type="hidden" name="codebase_path" value="{escape(state.codebase_path)}">'
        f'<input type="hidden" name="notes_path" value="{escape(state.notes_path or '')}">'
    )


def render_workflow_header(active_view: str) -> str:
    view = VIEW_CONFIG.get(active_view, VIEW_CONFIG["assistant"])
    return (
        f'<section class="workflow-header panel">'
        f'<div><p class="eyebrow">{escape(view["eyebrow"])}</p>'
        f'<h2>{escape(view["title"])}</h2></div>'
        f'<p>{escape(view["hint"])}</p>'
        f'</section>'
    )


def render_workflow_nav(active_view: str) -> str:
    links = []
    for name, view in VIEW_CONFIG.items():
        active = " active" if name == active_view else ""
        links.append(
            f'<a class="workflow-button{active}" href="{escape(view["path"])}">{escape(view["label"])}</a>'
        )
    return '<nav class="actions workflow-nav" aria-label="工作流页面">' + "".join(links) + "</nav>"


def render_notes(notes: list[dict[str, Any]]) -> str:
    if not notes:
        return '<p class="empty">还没有笔记。运行助手或手动记录一个发现。</p>'

    items = []
    for note in notes:
        items.append(
            f"""<article class="note">
  <div>
    <strong>{escape(note.get("title", "Untitled"))}</strong>
    <span>{escape(note.get("type", "general"))} · {escape(note.get("updated_at", ""))}</span>
  </div>
  <p>{escape(note.get("content", ""))}</p>
</article>"""
        )
    return "\n".join(items)


def render_error(error: str) -> str:
    if not error:
        return ""
    return f'<section class="error">{escape(error)}</section>'


def render_not_found() -> str:
    return "<!doctype html><title>Not Found</title><h1>Not Found</h1>"


def options(values: list[str], selected: str) -> str:
    return "\n".join(
        f'<option value="{escape(value)}"{" selected" if value == selected else ""}>{escape(value)}</option>'
        for value in values
    )


def first(fields: dict[str, list[str]], name: str, default: str) -> str:
    values = fields.get(name)
    if not values:
        return default
    return values[-1] or default


def safe_return_path(path: str) -> str:
    return path if path in PATH_TO_VIEW else "/assistant"


CSS = """
:root {
  color-scheme: light dark;
  --ink: #181713;
  --muted: #69645a;
  --line: rgba(52, 48, 40, 0.18);
  --paper: #f3ede2;
  --panel: rgba(255, 250, 240, 0.78);
  --panel-solid: #fffaf0;
  --accent: #256f5b;
  --accent-dark: #143f33;
  --accent-soft: rgba(37, 111, 91, 0.15);
  --warn: #9b3328;
  --code: #22201c;
  --shadow: rgba(48, 40, 28, 0.12);
  --spot-x: 50vw;
  --spot-y: 20vh;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100dvh;
  background:
    radial-gradient(circle at var(--spot-x) var(--spot-y), rgba(37, 111, 91, 0.13), transparent 24rem),
    linear-gradient(90deg, rgba(24, 23, 19, 0.05) 1px, transparent 1px),
    linear-gradient(0deg, rgba(24, 23, 19, 0.04) 1px, transparent 1px),
    var(--paper);
  background-size: auto, 36px 36px, 36px 36px, auto;
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  overflow-x: hidden;
}

body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    linear-gradient(115deg, transparent 0 34%, rgba(37, 111, 91, 0.08) 44%, transparent 56% 100%),
    repeating-linear-gradient(0deg, rgba(24, 23, 19, 0.035) 0 1px, transparent 1px 5px);
  mix-blend-mode: multiply;
}

.ambient-canvas,
.background-scan {
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.ambient-canvas {
  z-index: 0;
  opacity: 0.78;
}

.background-scan {
  z-index: 1;
  background: linear-gradient(180deg, transparent 0%, rgba(37, 111, 91, 0.08) 48%, transparent 100%);
  transform: translateY(-120%);
  opacity: 0.7;
}

.shell {
  position: relative;
  z-index: 2;
  width: min(1440px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 24px 0 48px;
}

.topbar {
  min-height: 72px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  border-bottom: 1px solid var(--line);
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1, h2 {
  margin: 0;
  letter-spacing: 0;
}

h1 {
  font-size: clamp(34px, 6vw, 72px);
  line-height: 0.95;
}

h2 {
  font-size: 18px;
}

.status-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 0 14px;
  border: 1px solid rgba(37, 111, 91, 0.22);
  border-radius: 999px;
  background: rgba(255, 250, 240, 0.68);
  backdrop-filter: blur(18px) saturate(150%);
  -webkit-backdrop-filter: blur(18px) saturate(150%);
  color: var(--muted);
  font-size: 14px;
  white-space: nowrap;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.42);
}

.status-pill span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 4px rgba(37, 111, 91, 0.12);
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 16px;
  margin-top: 20px;
}

.workflow-header {
  display: grid;
  grid-template-columns: minmax(0, 0.75fr) minmax(260px, 0.55fr);
  align-items: end;
  gap: 20px;
  margin-top: 18px;
}

.workflow-header h2 {
  font-size: clamp(28px, 4vw, 52px);
  line-height: 1;
}

.workflow-header p:last-child {
  margin: 0;
  color: var(--muted);
  line-height: 1.6;
}

.workflow-nav {
  margin-bottom: 14px;
}

.workflow-button {
  --button-x: 50%;
  --button-y: 50%;
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  overflow: hidden;
  border: 1px solid rgba(37, 111, 91, 0.32);
  border-radius: 9px;
  background:
    radial-gradient(circle at var(--button-x) var(--button-y), rgba(37, 111, 91, 0.13), transparent 34%),
    rgba(255, 253, 248, 0.88);
  color: var(--accent-dark);
  font-weight: 850;
  padding: 0 16px;
  text-decoration: none;
  box-shadow: 0 8px 20px rgba(48, 40, 28, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.44);
  transition: transform 180ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 180ms ease, border-color 180ms ease, background 180ms ease;
}

.workflow-button::before {
  content: "";
  position: absolute;
  inset: -35% -55%;
  z-index: -1;
  background: linear-gradient(110deg, transparent 38%, rgba(255, 255, 255, 0.34) 48%, transparent 58%);
  transform: translateX(-60%) rotate(8deg);
  transition: transform 520ms cubic-bezier(0.16, 1, 0.3, 1);
}

.workflow-button:hover {
  transform: translateY(-2px) scale(1.015);
  border-color: rgba(37, 111, 91, 0.62);
  box-shadow: 0 16px 34px rgba(23, 72, 56, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.32);
}

.workflow-button:hover::before {
  transform: translateX(54%) rotate(8deg);
}

.workflow-button.active {
  border-color: rgba(37, 111, 91, 0.72);
  background:
    radial-gradient(circle at var(--button-x) var(--button-y), rgba(255, 255, 255, 0.26), transparent 32%),
    linear-gradient(135deg, #2d8069, var(--accent-dark));
  color: #fffaf0;
  box-shadow: 0 14px 30px rgba(23, 72, 56, 0.26), inset 0 1px 0 rgba(255, 255, 255, 0.24);
}

.run-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.run-row span {
  max-width: 58ch;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}

.view-stage {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  margin-top: 16px;
}

.stage-primary {
  min-height: 390px;
}

.mode-card {
  display: grid;
  align-content: start;
  gap: 16px;
}

.page-sigil {
  width: 100%;
  min-height: 82px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(37, 111, 91, 0.2);
  border-radius: 10px;
  background:
    radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.34), transparent 28%),
    linear-gradient(135deg, rgba(37, 111, 91, 0.18), rgba(37, 111, 91, 0.04));
  color: var(--accent-dark);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 22px;
  font-weight: 900;
}

.flow-list {
  display: grid;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.flow-list li {
  display: grid;
  gap: 5px;
  border-left: 3px solid var(--accent);
  padding-left: 12px;
}

.flow-list span,
.mode-summary,
.mini-notes span {
  color: var(--muted);
  line-height: 1.5;
}

.map-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(70px, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.map-strip b {
  min-height: 42px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(37, 111, 91, 0.22);
  border-radius: 9px;
  background: rgba(37, 111, 91, 0.09);
}

.explore-stage .stage-primary {
  background:
    linear-gradient(135deg, rgba(37, 111, 91, 0.12), transparent 38%),
    var(--panel);
}

.analyze-stage {
  grid-template-columns: minmax(0, 0.74fr) minmax(340px, 0.46fr);
}

.analyze-stage .stage-primary {
  background:
    linear-gradient(135deg, rgba(155, 51, 40, 0.1), transparent 38%),
    var(--panel);
}

.risk-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.risk-grid div {
  min-height: 96px;
  display: grid;
  align-content: space-between;
  border: 1px solid rgba(155, 51, 40, 0.22);
  border-radius: 10px;
  background: rgba(155, 51, 40, 0.07);
  padding: 12px;
}

.risk-grid strong {
  color: var(--warn);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.plan-stage {
  grid-template-columns: minmax(0, 1fr) 330px;
}

.plan-stage .stage-primary {
  background:
    linear-gradient(135deg, rgba(37, 111, 91, 0.08), transparent 28%),
    repeating-linear-gradient(90deg, rgba(37, 111, 91, 0.08) 0 1px, transparent 1px 140px),
    var(--panel);
}

.task-board {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.task-board section {
  min-height: 250px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(255, 253, 248, 0.58);
  padding: 12px;
}

.task-board h3 {
  margin: 0 0 12px;
  font-size: 15px;
}

.task-board p,
.mini-notes p {
  margin: 0 0 9px;
  border: 1px solid rgba(37, 111, 91, 0.16);
  border-radius: 8px;
  background: rgba(255, 253, 248, 0.62);
  padding: 10px;
}

.mini-notes {
  display: grid;
  gap: 8px;
}

.mini-notes p {
  display: grid;
  gap: 4px;
}

.assistant-stage .stage-primary {
  background:
    linear-gradient(135deg, rgba(37, 111, 91, 0.16), transparent 34%),
    var(--panel);
}

.panel {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 10px;
  background:
    radial-gradient(circle at var(--card-x, 50%) var(--card-y, 0%), rgba(37, 111, 91, 0.12), transparent 18rem),
    var(--panel);
  backdrop-filter: blur(18px) saturate(145%);
  -webkit-backdrop-filter: blur(18px) saturate(145%);
  box-shadow: 0 18px 44px var(--shadow), inset 0 1px 0 rgba(255, 255, 255, 0.42);
  padding: 18px;
  transition: transform 220ms cubic-bezier(0.16, 1, 0.3, 1), border-color 220ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 220ms cubic-bezier(0.16, 1, 0.3, 1);
}

.panel::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  border-radius: inherit;
  padding: 1px;
  background: radial-gradient(circle at var(--card-x, 50%) var(--card-y, 0%), rgba(37, 111, 91, 0.42), transparent 38%);
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  -webkit-mask-composite: xor;
  opacity: 0;
  transition: opacity 220ms ease;
}

.panel:hover {
  transform: translateY(-2px);
  border-color: rgba(37, 111, 91, 0.34);
  box-shadow: 0 22px 58px rgba(48, 40, 28, 0.16), inset 0 1px 0 rgba(255, 255, 255, 0.54);
}

.panel:hover::before {
  opacity: 1;
}

.panel-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.panel-heading span {
  color: var(--muted);
  font-size: 13px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

label {
  display: grid;
  gap: 7px;
  margin-bottom: 14px;
}

label span {
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}

input, select, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 253, 248, 0.86);
  color: var(--ink);
  font: inherit;
  padding: 11px 12px;
  outline: none;
  transition: border-color 180ms ease, box-shadow 180ms ease, transform 180ms ease, background 180ms ease;
}

textarea {
  resize: vertical;
}

input:focus, select:focus, textarea:focus {
  border-color: var(--accent);
  background: rgba(255, 253, 248, 0.98);
  box-shadow: 0 0 0 3px rgba(37, 111, 91, 0.14);
  transform: translateY(-1px);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

button {
  --button-x: 50%;
  --button-y: 50%;
  position: relative;
  isolation: isolate;
  min-height: 42px;
  overflow: hidden;
  border: 1px solid rgba(37, 111, 91, 0.55);
  border-radius: 9px;
  background:
    radial-gradient(circle at var(--button-x) var(--button-y), rgba(255, 255, 255, 0.28), transparent 32%),
    linear-gradient(135deg, #2d8069, var(--accent-dark));
  color: #fffaf0;
  font: inherit;
  font-weight: 850;
  padding: 0 16px;
  cursor: pointer;
  box-shadow: 0 10px 22px rgba(23, 72, 56, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.24);
  transform: translate3d(0, 0, 0);
  transition: transform 180ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 180ms ease, border-color 180ms ease;
}

button::before {
  content: "";
  position: absolute;
  inset: -35% -55%;
  z-index: -1;
  background: linear-gradient(110deg, transparent 38%, rgba(255, 255, 255, 0.34) 48%, transparent 58%);
  transform: translateX(-60%) rotate(8deg);
  transition: transform 520ms cubic-bezier(0.16, 1, 0.3, 1);
}

button::after {
  content: "";
  position: absolute;
  inset: 1px;
  z-index: -1;
  border-radius: 7px;
  border: 1px solid rgba(255, 255, 255, 0.14);
}

button:hover {
  transform: translateY(-2px) scale(1.015);
  border-color: rgba(37, 111, 91, 0.76);
  box-shadow: 0 16px 34px rgba(23, 72, 56, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.32);
}

button:hover::before {
  transform: translateX(54%) rotate(8deg);
}

button:active {
  transform: translateY(1px) scale(0.985);
  box-shadow: 0 6px 14px rgba(23, 72, 56, 0.22), inset 0 2px 8px rgba(20, 63, 51, 0.28);
}

button.secondary {
  background:
    radial-gradient(circle at var(--button-x) var(--button-y), rgba(37, 111, 91, 0.13), transparent 34%),
    rgba(255, 253, 248, 0.9);
  color: var(--accent-dark);
  box-shadow: 0 8px 20px rgba(48, 40, 28, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.44);
}

.metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  align-content: start;
}

.metrics > div {
  border-top: 3px solid var(--accent);
  padding-top: 12px;
}

.metrics strong {
  display: block;
  margin-top: 6px;
  font-size: 34px;
  line-height: 1;
}

.metric-label, .session span {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

.session,
.context-visual {
  grid-column: 1 / -1;
}

.context-visual {
  position: relative;
  min-height: 118px;
  border: 1px solid rgba(37, 111, 91, 0.16);
  border-radius: 10px;
  background:
    linear-gradient(90deg, rgba(37, 111, 91, 0.08), transparent),
    repeating-linear-gradient(90deg, rgba(37, 111, 91, 0.13) 0 1px, transparent 1px 38px);
  overflow: hidden;
}

.context-visual::before,
.context-visual::after {
  content: "";
  position: absolute;
  inset: 18px;
  border: 1px solid rgba(37, 111, 91, 0.22);
  border-radius: 50%;
  transform: rotate(-10deg);
}

.context-visual::after {
  inset: 34px 58px;
  transform: rotate(18deg);
  opacity: 0.74;
}

.context-visual i {
  position: absolute;
  left: calc(12% + var(--i) * 14%);
  top: calc(22% + (var(--i) % 3) * 18%);
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 5px rgba(37, 111, 91, 0.11);
}

code {
  display: block;
  margin-top: 7px;
  overflow-wrap: anywhere;
  color: var(--code);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

pre {
  min-height: 220px;
  max-height: 440px;
  overflow: auto;
  margin: 0;
  border: 1px solid rgba(236, 230, 216, 0.13);
  border-radius: 9px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.035), transparent 42%),
    #1f211d;
  color: #ece6d8;
  padding: 14px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.55;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.notes {
  margin-top: 16px;
}

.note-list {
  display: grid;
  gap: 10px;
}

.note {
  display: grid;
  gap: 8px;
  border-top: 1px solid var(--line);
  padding-top: 12px;
}

.note strong {
  display: block;
}

.note span {
  color: var(--muted);
  font-size: 12px;
}

.note p {
  max-height: 90px;
  overflow: hidden;
  margin: 0;
  color: var(--muted);
  line-height: 1.5;
}

.empty {
  margin: 0;
  color: var(--muted);
}

.error {
  margin-top: 16px;
  border: 1px solid rgba(155, 51, 40, 0.35);
  border-radius: 8px;
  background: rgba(155, 51, 40, 0.08);
  color: var(--warn);
  padding: 12px 14px;
  font-weight: 700;
}

@media (prefers-color-scheme: dark) {
  :root {
    --ink: #f0eadf;
    --muted: #b5aa99;
    --line: rgba(240, 234, 223, 0.14);
    --paper: #171914;
    --panel: rgba(31, 33, 29, 0.76);
    --panel-solid: #20221d;
    --accent: #69b79f;
    --accent-dark: #225846;
    --accent-soft: rgba(105, 183, 159, 0.16);
    --warn: #e08b7f;
    --code: #f0eadf;
    --shadow: rgba(0, 0, 0, 0.24);
  }

  body {
    background:
      radial-gradient(circle at var(--spot-x) var(--spot-y), rgba(105, 183, 159, 0.13), transparent 24rem),
      linear-gradient(90deg, rgba(240, 234, 223, 0.035) 1px, transparent 1px),
      linear-gradient(0deg, rgba(240, 234, 223, 0.03) 1px, transparent 1px),
      var(--paper);
    background-size: auto, 36px 36px, 36px 36px, auto;
  }

  body::before {
    mix-blend-mode: screen;
    opacity: 0.42;
  }

  input, select, textarea {
    background: rgba(30, 32, 27, 0.88);
  }

  input:focus, select:focus, textarea:focus {
    background: rgba(30, 32, 27, 0.98);
  }

  button.secondary,
  .workflow-button,
  .task-board section,
  .task-board p,
  .mini-notes p {
    background: rgba(31, 33, 29, 0.88);
    color: #d8f2e8;
  }

  .workflow-button.active {
    color: #fffaf0;
  }

  .status-pill {
    background: rgba(31, 33, 29, 0.62);
  }
}

@media (prefers-reduced-motion: no-preference) {
  .background-scan {
    animation: scan-field 8s cubic-bezier(0.16, 1, 0.3, 1) infinite;
  }

  .status-pill span {
    animation: status-breathe 2.6s ease-in-out infinite;
  }

  .panel {
    animation: panel-enter 520ms cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  .workspace-grid:nth-of-type(2) .panel {
    animation-delay: 80ms;
  }

  .workspace-grid:nth-of-type(3) .panel {
    animation-delay: 140ms;
  }

  .context-visual::before {
    animation: orbit-slow 9s linear infinite;
  }

  .context-visual::after {
    animation: orbit-slow 12s linear reverse infinite;
  }

  .context-visual i {
    animation: node-pulse 2.4s ease-in-out infinite;
    animation-delay: calc(var(--i) * 160ms);
  }
}

@keyframes scan-field {
  0% { transform: translateY(-120%); opacity: 0; }
  20% { opacity: 0.7; }
  60% { opacity: 0.45; }
  100% { transform: translateY(120%); opacity: 0; }
}

@keyframes status-breathe {
  0%, 100% { box-shadow: 0 0 0 4px rgba(37, 111, 91, 0.12); }
  50% { box-shadow: 0 0 0 8px rgba(37, 111, 91, 0.04); }
}

@keyframes panel-enter {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes orbit-slow {
  to { transform: rotate(350deg); }
}

@keyframes node-pulse {
  0%, 100% { transform: scale(1); opacity: 0.9; }
  50% { transform: scale(1.45); opacity: 0.55; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.001ms !important;
  }

  .ambient-canvas,
  .background-scan {
    display: none;
  }
}

@media (max-width: 900px) {
  .hero,
  .workspace-grid,
  .workflow-header,
  .view-stage,
  .analyze-stage,
  .plan-stage {
    grid-template-columns: 1fr;
  }

  .task-board,
  .map-strip {
    grid-template-columns: 1fr;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .shell {
    width: min(100vw - 20px, 1440px);
    padding-top: 12px;
  }

  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }

  h1 {
    font-size: 42px;
  }

  .metrics {
    grid-template-columns: 1fr;
  }
}
"""


SCRIPT = """
(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const root = document.documentElement;

  window.addEventListener('pointermove', (event) => {
    root.style.setProperty('--spot-x', `${event.clientX}px`);
    root.style.setProperty('--spot-y', `${event.clientY}px`);
  }, { passive: true });

  document.querySelectorAll('.panel').forEach((panel) => {
    panel.addEventListener('pointermove', (event) => {
      const rect = panel.getBoundingClientRect();
      panel.style.setProperty('--card-x', `${event.clientX - rect.left}px`);
      panel.style.setProperty('--card-y', `${event.clientY - rect.top}px`);
    }, { passive: true });
  });

  document.querySelectorAll('button, .workflow-button').forEach((button) => {
    button.addEventListener('pointermove', (event) => {
      const rect = button.getBoundingClientRect();
      button.style.setProperty('--button-x', `${event.clientX - rect.left}px`);
      button.style.setProperty('--button-y', `${event.clientY - rect.top}px`);
    }, { passive: true });
  });

  const canvas = document.getElementById('ambient-canvas');
  if (!canvas || reduce) return;
  const ctx = canvas.getContext('2d');
  let width = 0;
  let height = 0;
  let nodes = [];
  const words = ['Gather', 'Select', 'Structure', 'Compress', 'Note', 'Terminal', 'Context'];

  function resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    nodes = Array.from({ length: Math.min(46, Math.max(24, Math.floor(width / 34))) }, (_, index) => ({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.18,
      vy: (Math.random() - 0.5) * 0.18,
      r: 1.5 + Math.random() * 2.2,
      label: index % 7 === 0 ? words[index % words.length] : ''
    }));
  }

  function draw() {
    ctx.clearRect(0, 0, width, height);
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const line = dark ? 'rgba(105, 183, 159, 0.15)' : 'rgba(37, 111, 91, 0.13)';
    const dot = dark ? 'rgba(186, 232, 218, 0.62)' : 'rgba(37, 111, 91, 0.5)';
    const text = dark ? 'rgba(240, 234, 223, 0.36)' : 'rgba(24, 23, 19, 0.28)';

    nodes.forEach((node) => {
      node.x += node.vx;
      node.y += node.vy;
      if (node.x < -20) node.x = width + 20;
      if (node.x > width + 20) node.x = -20;
      if (node.y < -20) node.y = height + 20;
      if (node.y > height + 20) node.y = -20;
    });

    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 150) {
          ctx.globalAlpha = (1 - dist / 150) * 0.8;
          ctx.strokeStyle = line;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    ctx.globalAlpha = 1;
    nodes.forEach((node) => {
      ctx.fillStyle = dot;
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fill();
      if (node.label) {
        ctx.fillStyle = text;
        ctx.font = '12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
        ctx.fillText(node.label, node.x + 8, node.y - 8);
      }
    });

    requestAnimationFrame(draw);
  }

  resize();
  window.addEventListener('resize', resize, { passive: true });
  requestAnimationFrame(draw);
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
