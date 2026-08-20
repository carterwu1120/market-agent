"""Local knowledge base — plain files, no embedding/retrieval needed.

data/knowledge_base/ holds a handful of personal notes, small enough to just
read in full rather than chunk + embed + vector-search. If this ever grows
past what fits in Claude's context window, that's when re-introducing
retrieval would make sense — not before.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_KB_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge_base"


def read_knowledge_base(directory: Path = _KB_DIR) -> str:
    """Concatenate every .md/.txt file in the knowledge base into one string.

    Excludes README.md (that's documentation for the directory itself, not
    content). Returns "" if the directory is empty or unreadable. Each file
    read is guarded individually so one bad file doesn't lose the rest.
    """
    files = [
        f for f in list(directory.rglob("*.md")) + list(directory.rglob("*.txt"))
        if f.name.upper() != "README.MD"
    ]
    if not files:
        return ""

    parts = []
    for f in sorted(files):
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning(f"knowledge_base: failed to read {f}: {exc}")
            continue
        parts.append(f"## {f.name}\n\n{content}")
    return "\n\n---\n\n".join(parts)
