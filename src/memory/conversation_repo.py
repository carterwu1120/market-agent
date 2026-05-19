"""PostgreSQL persistence for conversations and messages."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.memory.models import Conversation, Message, User


async def get_or_create_user(session: AsyncSession, discord_id: int, username: str) -> User:
    result = await session.execute(select(User).where(User.id == discord_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(id=discord_id, username=username)
        session.add(user)
        await session.commit()
    return user


async def get_or_create_conversation(
    session: AsyncSession, user_id: int, channel_id: str
) -> Conversation:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.channel_id == channel_id)
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        conv = Conversation(user_id=user_id, channel_id=channel_id)
        session.add(conv)
        await session.commit()
    return conv


async def save_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    meta: dict | None = None,
) -> Message:
    msg = Message(conversation_id=conversation_id, role=role, content=content, meta=meta or {})
    session.add(msg)
    await session.commit()
    return msg


async def get_recent_messages(
    session: AsyncSession, conversation_id: int, limit: int = 20
) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))
