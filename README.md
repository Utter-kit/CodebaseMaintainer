# CodebaseMaintainer

`CodebaseMaintainer` 是代码库维护助手”的可运行示例。它把 `ContextBuilder`、`NoteTool` 和 `TerminalTool` 组合成一个长期维护代码库的助手，用于探索项目结构、记录技术债、追踪重构任务，并在上下文窗口有限时保持高信号密度。

## 能力

- 按需探索代码库结构，例如使用 `find`、`grep`、`wc` 收集局部证据。
- 将发现的问题、任务计划、阻塞和结论保存为跨会话 JSON 笔记。
- 使用 GSSC 流程构建上下文：Gather、Select、Structure、Compress。
- 提供 `auto`、`explore`、`analyze`、`plan` 四种运行模式。
- 提供可测试的本地启发式 LLM fallback，便于教学演示和 CI 验证。

## 架构

```text
Application Layer
└── CodebaseMaintainer
    ├── 会话管理
    ├── 任务协调
    └── 决策逻辑

Context Management Layer
└── ContextBuilder
    ├── Gather: 汇集系统指令、历史对话、笔记、终端输出
    ├── Select: 按相关性和新近性选择信息
    ├── Structure: 组织为标准上下文模板
    └── Compress: 在 token 超限时分段压缩

Tool Layer
├── TerminalTool: 即时文件访问和代码探索
└── NoteTool: 持久化记录任务状态、发现问题、重构计划和关键决策
```

## 安装与运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

探索一个代码库：

```bash
codebase-maintainer /path/to/flask_app "请探索这个 Flask 项目的结构" --mode explore
```

分析代码质量：

```bash
codebase-maintainer /path/to/flask_app "请查找 TODO、复杂模块和缺失测试" --mode analyze
```

生成会话报告：

```bash
codebase-maintainer /path/to/flask_app --mode plan --report maintainer_report.json
```

## Python 用法

```python
from codebase_maintainer import CodebaseMaintainer

assistant = CodebaseMaintainer(
    project_name="my_flask_app",
    codebase_path="/path/to/flask_app",
)

print(assistant.explore())
print(assistant.analyze("API 路由和服务层"))
print(assistant.plan_next_steps())
```

## 项目结构

```text
codebase_maintainer/
├── assistant.py      # 代码库维护助手主流程
├── cli.py            # 命令行入口
├── context.py        # ContextPacket、ContextConfig、ContextBuilder
└── tools.py          # NoteTool、TerminalTool

tests/
├── test_assistant.py
├── test_context.py
└── test_tools.py
```

根目录中的 `CodebaseMaintainer.py`、`ContextBuilder.py`、`ContextConfig.py`、`ContextPacket.py`、`ContextNotetool.py` 保留为兼容导入入口。

## 测试

```bash
pytest
```
