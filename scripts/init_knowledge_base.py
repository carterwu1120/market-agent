"""Script to ingest knowledge base documents into the local SQLite knowledge store.

Run once (and again whenever you add new files to data/knowledge_base):
  uv run python scripts/init_knowledge_base.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory.store import init_storage
from src.rag.knowledge_store import ingest_directory


async def main():
    print("Initializing storage...")
    await init_storage()

    kb_path = Path(__file__).parent.parent / "data" / "knowledge_base"
    print(f"Ingesting knowledge base from: {kb_path}")

    await ingest_directory(kb_path)

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
