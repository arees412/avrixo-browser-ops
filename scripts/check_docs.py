"""Validate local Markdown links and balanced Mermaid fences."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    docs_root = ROOT / "docs"
    return sorted(path for path in ROOT.rglob("*.md") if path.parent == ROOT or docs_root in path.parents)


def main() -> int:
    errors: list[str] = []
    files = markdown_files()
    for path in files:
        text = path.read_text(encoding="utf-8")
        if text.count("```mermaid") > text.count("```"):
            errors.append(f"{path.relative_to(ROOT)}: unbalanced Mermaid fence")
        for match in LINK.finditer(text):
            raw = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
            if not raw or raw.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = unquote(raw.split("#", 1)[0])
            if relative and not (path.parent / relative).resolve().exists():
                errors.append(f"{path.relative_to(ROOT)}: missing link target {relative}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"documentation check passed ({len(files)} Markdown files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
