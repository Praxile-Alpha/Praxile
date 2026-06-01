from __future__ import annotations

from pathlib import Path
import sys

from .config import find_project_root
from .cli_parser import build_parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = find_project_root(Path(args.project).resolve()) if getattr(args, "project", None) else find_project_root(Path.cwd())
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.func(args, project_root)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
