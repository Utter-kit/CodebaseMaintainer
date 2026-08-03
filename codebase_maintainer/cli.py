from __future__ import annotations

import argparse
import json

from .assistant import CodebaseMaintainer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the codebase maintenance assistant.")
    parser.add_argument("codebase_path", help="Path to the codebase to inspect.")
    parser.add_argument("query", nargs="?", default="请探索这个代码库并给出维护建议。")
    parser.add_argument("--project-name", default="my_flask_app")
    parser.add_argument("--mode", choices=sorted(CodebaseMaintainer.VALID_MODES), default="auto")
    parser.add_argument("--notes-path", default=None)
    parser.add_argument("--report", default=None, help="Optional JSON report output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assistant = CodebaseMaintainer(
        project_name=args.project_name,
        codebase_path=args.codebase_path,
        notes_path=args.notes_path,
    )
    response = assistant.run(args.query, mode=args.mode)
    print(response)
    if args.report:
        print(json.dumps(assistant.generate_report(args.report), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
