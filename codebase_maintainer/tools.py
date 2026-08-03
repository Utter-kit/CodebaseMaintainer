from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from uuid import uuid4


@dataclass
class TerminalResult:
    command: str
    stdout: str
    stderr: str
    returncode: int

    def as_text(self) -> str:
        parts = [f"$ {self.command}"]
        if self.stdout:
            parts.append(self.stdout.rstrip())
        if self.stderr:
            parts.append("[stderr]\n" + self.stderr.rstrip())
        if self.returncode != 0:
            parts.append(f"[exit_code] {self.returncode}")
        return "\n".join(parts)


class TerminalTool:
    """Small, workspace-scoped terminal wrapper for code exploration."""

    def __init__(self, workspace: str | os.PathLike[str], timeout: int = 30) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.timeout = timeout
        if not self.workspace.exists():
            raise FileNotFoundError(f"workspace does not exist: {self.workspace}")

    def run(self, payload: dict[str, Any] | str) -> str:
        command = payload if isinstance(payload, str) else payload.get("command", "")
        if not command.strip():
            raise ValueError("command is required")
        result = self.execute(command)
        return result.as_text()

    def execute(self, command: str) -> TerminalResult:
        completed = subprocess.run(
            command,
            cwd=self.workspace,
            shell=True,
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        return TerminalResult(
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )


class NoteTool:
    """JSON-file backed notes for cross-session state."""

    VALID_TYPES = {"general", "task_state", "blocker", "action", "conclusion", "finding"}

    def __init__(self, workspace: str | os.PathLike[str]) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def run(self, payload: dict[str, Any]) -> Any:
        action = payload.get("action")
        if action == "create":
            return self.create(
                title=payload["title"],
                content=payload["content"],
                note_type=payload.get("note_type", "general"),
                tags=payload.get("tags", []),
            )
        if action == "list":
            return self.list(
                note_type=payload.get("note_type"),
                limit=int(payload.get("limit", 20)),
            )
        if action == "search":
            return self.search(
                query=payload.get("query", ""),
                limit=int(payload.get("limit", 10)),
            )
        if action == "summary":
            return self.summary()
        if action == "update":
            return self.update(payload["note_id"], **payload.get("fields", {}))
        raise ValueError(f"unknown note action: {action}")

    def create(
        self,
        title: str,
        content: str,
        note_type: str = "general",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if note_type not in self.VALID_TYPES:
            note_type = "general"
        now = datetime.now().isoformat(timespec="seconds")
        note = {
            "note_id": uuid4().hex,
            "title": title,
            "content": content,
            "type": note_type,
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
        }
        self._write(note)
        return note

    def list(self, note_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        notes = self._read_all()
        if note_type:
            notes = [note for note in notes if note.get("type") == note_type]
        notes.sort(key=lambda note: note.get("updated_at", ""), reverse=True)
        return notes[:limit]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        terms = [term.lower() for term in query.split() if term.strip()]
        if not terms:
            return self.list(limit=limit)

        scored: list[tuple[int, dict[str, Any]]] = []
        for note in self._read_all():
            haystack = " ".join(
                [
                    note.get("title", ""),
                    note.get("content", ""),
                    note.get("type", ""),
                    " ".join(note.get("tags", [])),
                ]
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((score, note))

        scored.sort(key=lambda item: (item[0], item[1].get("updated_at", "")), reverse=True)
        return [note for _, note in scored[:limit]]

    def update(self, note_id: str, **fields: Any) -> dict[str, Any]:
        note = self._read(note_id)
        allowed = {"title", "content", "type", "tags"}
        for key, value in fields.items():
            if key in allowed:
                note[key] = value
        note["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write(note)
        return note

    def summary(self) -> dict[str, Any]:
        notes = self._read_all()
        by_type: dict[str, int] = {}
        for note in notes:
            by_type[note.get("type", "general")] = by_type.get(note.get("type", "general"), 0) + 1
        latest = sorted(notes, key=lambda note: note.get("updated_at", ""), reverse=True)[:5]
        return {
            "total": len(notes),
            "by_type": by_type,
            "latest": [{"note_id": note["note_id"], "title": note["title"]} for note in latest],
        }

    def _path(self, note_id: str) -> Path:
        return self.workspace / f"{note_id}.json"

    def _write(self, note: dict[str, Any]) -> None:
        self._path(note["note_id"]).write_text(
            json.dumps(note, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read(self, note_id: str) -> dict[str, Any]:
        path = self._path(note_id)
        if not path.exists():
            raise FileNotFoundError(f"note not found: {note_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_all(self) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        for path in self.workspace.glob("*.json"):
            try:
                notes.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return notes
