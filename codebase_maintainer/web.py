from __future__ import annotations

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .assistant import CodebaseMaintainer


DEFAULT_QUERY = "请探索这个代码库并给出维护建议。"


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
        if parsed.path != "/":
            self._send_html(render_not_found(), status=404)
            return
        self._send_html(render_page(self.state))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        fields = self._read_form()
        try:
            if parsed.path == "/run":
                self._configure_from_form(fields)
                query = first(fields, "query", DEFAULT_QUERY)
                mode = first(fields, "mode", "auto")
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

        self._redirect("/")

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


def render_page(state: WebState) -> str:
    stats = state.assistant.get_stats()
    notes = state.assistant.note_tool.list(limit=8)
    files_output = safe_command(
        state.assistant,
        "find . -path ./.git -prune -o -path '*/__pycache__' -prune -o -maxdepth 3 -type f -print | sort | head -n 80",
    )
    mode_options = options(
        ["auto", "explore", "analyze", "plan"],
        "auto",
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

    <section class="hero">
      <form class="panel control-panel" method="post" action="/run">
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
          <span>任务</span>
          <textarea name="query" rows="4">{escape(DEFAULT_QUERY)}</textarea>
        </label>
        <div class="actions">
          <button type="submit">运行助手</button>
          <button type="submit" name="mode" value="explore" class="secondary">探索结构</button>
          <button type="submit" name="mode" value="analyze" class="secondary">分析质量</button>
          <button type="submit" name="mode" value="plan" class="secondary">规划任务</button>
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
      </aside>
    </section>

    <section class="workspace-grid">
      <article class="panel output">
        <div class="panel-heading">
          <h2>助手输出</h2>
          <span>ContextBuilder + NoteTool + TerminalTool</span>
        </div>
        <pre>{escape(state.last_response or "运行一次助手后，这里会显示维护建议。")}</pre>
      </article>

      <article class="panel output">
        <div class="panel-heading">
          <h2>代码库快照</h2>
          <span>最多显示 80 个文件</span>
        </div>
        <pre>{escape(files_output)}</pre>
      </article>
    </section>

    <section class="workspace-grid">
      <form class="panel" method="post" action="/command">
        {hidden_config_fields(state)}
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
        {hidden_config_fields(state)}
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
</body>
</html>"""


def safe_command(assistant: CodebaseMaintainer, command: str) -> str:
    try:
        return assistant.terminal_tool.run({"command": command})
    except Exception as exc:
        return f"无法读取代码库快照: {exc}"


def hidden_config_fields(state: WebState) -> str:
    return (
        f'<input type="hidden" name="project_name" value="{escape(state.project_name)}">'
        f'<input type="hidden" name="codebase_path" value="{escape(state.codebase_path)}">'
        f'<input type="hidden" name="notes_path" value="{escape(state.notes_path or '')}">'
    )


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


CSS = """
:root {
  color-scheme: light;
  --ink: #181713;
  --muted: #6c685f;
  --line: #ded8cb;
  --paper: #f6f1e8;
  --panel: #fffaf0;
  --accent: #256f5b;
  --accent-dark: #174838;
  --warn: #9b3328;
  --code: #22201c;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100dvh;
  background:
    linear-gradient(90deg, rgba(24, 23, 19, 0.045) 1px, transparent 1px),
    linear-gradient(0deg, rgba(24, 23, 19, 0.04) 1px, transparent 1px),
    var(--paper);
  background-size: 32px 32px;
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.shell {
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
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(255, 250, 240, 0.74);
  color: var(--muted);
  font-size: 14px;
  white-space: nowrap;
}

.status-pill span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  margin-top: 20px;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}

.panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 250, 240, 0.92);
  padding: 18px;
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
  background: #fffdf8;
  color: var(--ink);
  font: inherit;
  padding: 11px 12px;
  outline: none;
}

textarea {
  resize: vertical;
}

input:focus, select:focus, textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(37, 111, 91, 0.14);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

button {
  min-height: 40px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font: inherit;
  font-weight: 800;
  padding: 0 14px;
  cursor: pointer;
}

button.secondary {
  background: #fffdf8;
  color: var(--accent-dark);
}

button:active {
  transform: translateY(1px);
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

.session {
  grid-column: 1 / -1;
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
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #1f211d;
  color: #ece6d8;
  padding: 14px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.55;
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

@media (max-width: 900px) {
  .hero,
  .workspace-grid {
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


if __name__ == "__main__":
    raise SystemExit(main())
