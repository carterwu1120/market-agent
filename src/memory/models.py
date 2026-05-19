"""SQLAlchemy ORM models for PostgreSQL."""

from datetime import datetime
from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Index, Integer,
    String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Discord user ID
    username: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict)

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    channel_id: Mapped[str] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(Integer, ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))   # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)   # agent name, sources, etc.

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class NewsItem(Base):
    """Deduplicated news cache + vector embedding for RAG."""
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    source: Mapped[str] = mapped_column(String(100))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    tickers: Mapped[list] = mapped_column(JSONB, default=list)  # ["2330.TW", "TSMC"]
    embedding: Mapped[Vector] = mapped_column(Vector(1024), nullable=True)  # bge-m3 dim

    __table_args__ = (
        Index("ix_news_embedding", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64},
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class KnowledgeChunk(Base):
    """RAG knowledge base: technical analysis docs, market rules, etc."""
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(200), index=True)   # source filename / url
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    embedding: Mapped[Vector] = mapped_column(Vector(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_knowledge_embedding", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64},
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
