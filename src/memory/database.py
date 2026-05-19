"""Async SQLAlchemy engine + session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings

engine = create_async_engine(settings.postgres_dsn, echo=False, pool_size=10, max_overflow=20)

AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    async with AsyncSessionFactory() as session:
        yield session


async def init_db() -> None:
    """Create all tables and ensure pgvector extension exists."""
    from src.memory.models import Base
    async with engine.begin() as conn:
        await conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
