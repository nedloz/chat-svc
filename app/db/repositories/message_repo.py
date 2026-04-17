import uuid
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage


class MessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_message(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        content: str,
        reply_to_message_id: uuid.UUID | None = None,
        latency_ms: int | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
            reply_to_message_id=reply_to_message_id,
            latency_ms=latency_ms,
        )
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def update_content(self, message_id: uuid.UUID, content: str, latency_ms: int | None = None) -> None:
        message = await self.db.get(ChatMessage, message_id)
        if message is None:
            return
        message.content = content
        if latency_ms is not None:
            message.latency_ms = latency_ms
        await self.db.flush()

    async def get_by_id(self, message_id: uuid.UUID) -> ChatMessage | None:
        return await self.db.get(ChatMessage, message_id)

    async def list_recent(self, session_id: uuid.UUID, limit: int) -> list[ChatMessage]:
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def list_before_message(self, session_id: uuid.UUID, before_message_id: uuid.UUID, limit: int) -> list[ChatMessage]:
        anchor = await self.get_by_id(before_message_id)
        if anchor is None:
            return []
        result = await self.db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.created_at < anchor.created_at,
            )
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def serialize(self, message: ChatMessage) -> dict:
        return {
            'id': str(message.id),
            'session_id': str(message.session_id),
            'user_id': str(message.user_id),
            'role': message.role,
            'content': message.content,
            'reply_to_message_id': str(message.reply_to_message_id) if message.reply_to_message_id else None,
            'latency_ms': message.latency_ms,
            'created_at': message.created_at.isoformat() if isinstance(message.created_at, datetime) else None,
        }
