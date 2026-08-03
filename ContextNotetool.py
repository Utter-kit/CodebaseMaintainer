"""Backward-compatible imports for the project assistant note example."""

from codebase_maintainer.assistant import CodebaseMaintainer
from codebase_maintainer.tools import NoteTool

ProjectAssistant = CodebaseMaintainer

__all__ = ["CodebaseMaintainer", "ProjectAssistant", "NoteTool"]
