from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


DIR_NAMES = {
    ".praxile",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__MACOSX",
    "__pycache__",
    "dist",
    "build",
}
FILE_NAMES = {".DS_Store"}
FILE_SUFFIXES = {".pyc", ".pyo"}


def dirty_paths(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if any(part in {".git", ".venv", "venv"} for part in path.parts):
            continue
        if path.is_dir() and path.name in DIR_NAMES:
            found.append(path)
        elif path.is_file() and (path.name in FILE_NAMES or path.suffix in FILE_SUFFIXES):
            found.append(path)
    return sorted(found, key=lambda item: item.as_posix())


def clean(paths: list[Path]) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean local-only artifacts before building a Praxile release.")
    parser.add_argument("--check", action="store_true", help="Fail if local-only artifacts are present")
    parser.add_argument("--root", default=".", help="Repository root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    paths = dirty_paths(root)
    if args.check:
        if paths:
            print("Release artifact check failed; remove these local-only paths:")
            for path in paths:
                print(f"- {path.relative_to(root)}")
            return 1
        print("Release artifact check passed.")
        return 0
    clean(paths)
    print(f"Removed {len(paths)} local-only artifact(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
