"""Script to ingest knowledge base documents into pgvector.

Run once after docker compose up:
  python scripts/init_knowledge_base.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory.database import init_db, AsyncSessionFactory
from src.rag.knowledge_store import ingest_directory


async def main():
    print("Initializing database...")
    await init_db()

    kb_path = Path(__file__).parent.parent / "data" / "knowledge_base"
    print(f"Ingesting knowledge base from: {kb_path}")

    async with AsyncSessionFactory() as session:
        await ingest_directory(session, kb_path)

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
