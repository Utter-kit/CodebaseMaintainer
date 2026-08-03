from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Protocol

from .context import ContextBuilder, ContextConfig, ContextPacket
from .tools import NoteTool, TerminalTool


class LLMClient(Protocol):
    def invoke(self, prompt: str) -> str:
        ...


class HeuristicLLM:
    """Deterministic fallback used for local demos and tests."""

    def invoke(self, prompt: str) -> str:
        lines = prompt.splitlines()
        task = next((line for line in lines if line and not line.startswith("[")), "")
        return (
            "基于当前上下文，我建议先固定项目结构快照，再按风险排序处理问题。\n\n"
            f"当前任务: {task or '代码库维护'}\n"
            "下一步: 1. 运行结构扫描；2. 记录 blocker/action 笔记；"
            "3. 为高风险改动补测试；4. 完成后生成维护报告。"
        )


@dataclass
class Message:
    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class CodebaseMaintainer:
    """Long-running assistant that combines context, notes, and terminal access."""

    VALID_MODES = {"auto", "explore", "analyze", "plan"}

    def __init__(
        self,
        project_name: str,
        codebase_path: str,
        llm: LLMClient | None = None,
        notes_path: str | None = None,
        context_config: ContextConfig | None = None,
    ) -> None:
        self.project_name = project_name
        self.codebase_path = str(Path(codebase_path).expanduser().resolve())
        self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.llm = llm or HeuristicLLM()
        self.note_tool = NoteTool(notes_path or f"./{project_name}_notes")
        self.terminal_tool = TerminalTool(self.codebase_path, timeout=60)
        self.context_builder = ContextBuilder(context_config or ContextConfig())
        self.conversation_history: list[Message] = []
        self.stats: dict[str, Any] = {
            "session_start": datetime.now(),
            "commands_executed": 0,
            "notes_created": 0,
            "issues_found": 0,
        }

    def run(self, user_input: str, mode: str = "auto") -> str:
        if mode not in self.VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(self.VALID_MODES)}")

        pre_context = self._preprocess_by_mode(user_input, mode)
        relevant_notes = self._retrieve_relevant_notes(user_input)
        note_packets = self._notes_to_packets(relevant_notes)
        context = self.context_builder.build(
            user_query=user_input,
            conversation_history=self.conversation_history,
            system_instructions=self._build_system_instructions(mode),
            custom_packets=note_packets + pre_context,
        )
        response = self.llm.invoke(context)
        self._postprocess_response(user_input, response, mode)
        self._update_history(user_input, response)
        return response

    def explore(self, target: str = ".") -> str:
        return self.run(f"请探索 {target} 的代码结构", mode="explore")

    def analyze(self, focus: str = "") -> str:
        query = "请分析代码质量"
        if focus:
            query += f"，重点关注 {focus}"
        return self.run(query, mode="analyze")

    def plan_next_steps(self) -> str:
        return self.run("根据当前进度，规划下一步任务", mode="plan")

    def execute_command(self, command: str) -> str:
        result = self.terminal_tool.run({"command": command})
        self.stats["commands_executed"] += 1
        return result

    def create_note(
        self,
        title: str,
        content: str,
        note_type: str = "general",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        note = self.note_tool.create(
            title=title,
            content=content,
            note_type=note_type,
            tags=tags or [self.project_name],
        )
        self.stats["notes_created"] += 1
        return note

    def get_stats(self) -> dict[str, Any]:
        duration = (datetime.now() - self.stats["session_start"]).total_seconds()
        return {
            "session_info": {
                "session_id": self.session_id,
                "project": self.project_name,
                "codebase_path": self.codebase_path,
                "duration_seconds": duration,
            },
            "activity": {
                "commands_executed": self.stats["commands_executed"],
                "notes_created": self.stats["notes_created"],
                "issues_found": self.stats["issues_found"],
            },
            "notes": self.note_tool.summary(),
        }

    def generate_report(self, output_path: str | None = None) -> dict[str, Any]:
        report = self.get_stats()
        if output_path:
            path = Path(output_path)
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            report["report_file"] = str(path)
        return report

    def _preprocess_by_mode(self, user_input: str, mode: str) -> list[ContextPacket]:
        packets: list[ContextPacket] = []

        if mode in {"auto", "explore"}:
            structure = self.execute_command(
                "find . -type f -name '*.py' | sort | head -n 50"
            )
            packets.append(
                ContextPacket(
                    content=f"[代码库结构]\n{structure}",
                    relevance_score=0.65,
                    metadata={"type": "code_structure", "source": "terminal"},
                )
            )

        if mode == "analyze":
            loc = self.execute_command("find . -name '*.py' -exec wc -l {} +")
            todos = self.execute_command(
                "grep -RIn 'TODO\\|FIXME\\|XXX' --include='*.py' . | head -n 20"
            )
            packets.append(
                ContextPacket(
                    content=f"[代码统计]\n{loc}\n\n[待办事项]\n{todos}",
                    relevance_score=0.75,
                    metadata={"type": "code_analysis", "source": "terminal"},
                )
            )

        if mode == "plan":
            task_notes = self.note_tool.list(note_type="task_state", limit=5)
            content = "\n".join(f"- {note['title']}" for note in task_notes)
            if content:
                packets.append(
                    ContextPacket(
                        content=f"[当前任务]\n{content}",
                        relevance_score=0.85,
                        metadata={"type": "task_state", "source": "notes"},
                    )
                )

        return packets

    def _retrieve_relevant_notes(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        blockers = self.note_tool.list(note_type="blocker", limit=2)
        search_results = self.note_tool.search(query=query, limit=limit)
        unique: dict[str, dict[str, Any]] = {}
        for note in blockers + search_results:
            unique[note["note_id"]] = note
        return list(unique.values())[:limit]

    def _notes_to_packets(self, notes: list[dict[str, Any]]) -> list[ContextPacket]:
        relevance_map = {
            "blocker": 0.9,
            "action": 0.8,
            "task_state": 0.75,
            "finding": 0.75,
            "conclusion": 0.7,
        }
        packets: list[ContextPacket] = []
        for note in notes:
            note_type = note.get("type", "general")
            content = (
                f"[笔记: {note.get('title', 'Untitled')}]\n"
                f"类型: {note_type}\n"
                f"更新时间: {note.get('updated_at', '')}\n\n"
                f"{note.get('content', '')}"
            )
            packets.append(
                ContextPacket(
                    content=content,
                    timestamp=datetime.fromisoformat(note["updated_at"]),
                    relevance_score=relevance_map.get(note_type, 0.6),
                    metadata={
                        "type": "note",
                        "note_type": note_type,
                        "note_id": note.get("note_id"),
                    },
                )
            )
        return packets

    def _build_system_instructions(self, mode: str) -> str:
        return f"""你是 {self.project_name} 项目的代码库维护助手。

核心职责:
1. 使用 TerminalTool 按需探索代码库，而不是把全量代码塞进上下文。
2. 使用 NoteTool 记录发现的问题、长期任务、阻塞和关键决策。
3. 使用 ContextBuilder 汇集、筛选、组织并压缩上下文，保证高信号密度。
4. 给出具体、可验证、可延续到下一会话的维护建议。

当前会话: {self.session_id}
当前模式: {mode}
"""

    def _postprocess_response(self, user_input: str, response: str, mode: str) -> None:
        lower_response = response.lower()
        if any(keyword in lower_response for keyword in ["bug", "错误", "阻塞", "问题"]):
            self.create_note(
                title=f"发现问题: {user_input[:30]}",
                content=f"## 用户输入\n{user_input}\n\n## 助手分析\n{response[:800]}",
                note_type="blocker",
                tags=[self.project_name, "auto_detected", self.session_id],
            )
            self.stats["issues_found"] += 1
            return

        if mode == "plan" or any(keyword in user_input.lower() for keyword in ["计划", "下一步", "任务", "todo"]):
            self.create_note(
                title=f"任务规划: {user_input[:30]}",
                content=f"## 讨论\n{user_input}\n\n## 行动计划\n{response[:800]}",
                note_type="action",
                tags=[self.project_name, "planning", self.session_id],
            )

    def _update_history(self, user_input: str, response: str) -> None:
        self.conversation_history.append(Message(role="user", content=user_input))
        self.conversation_history.append(Message(role="assistant", content=response))
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]
