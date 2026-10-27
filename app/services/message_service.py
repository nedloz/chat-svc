import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics import MESSAGES
from app.db.repositories.message_repo import MessageRepository
from app.db.repositories.session_repo import SessionRepository
from app.services.redis_cache import RedisCacheService


class MessageService:
    def __init__(self, cache: RedisCacheService):
        self.cache = cache

    async def create_message(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        content: str,
        reply_to_message_id: uuid.UUID | None = None,
        latency_ms: int | None = None,
    ):
        message_repo = MessageRepository(db)
        session_repo = SessionRepository(db)
        message = await message_repo.create_message(
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
            reply_to_message_id=reply_to_message_id,
            latency_ms=latency_ms,
        )
        await session_repo.update_last_message(session_id, message.id, message.created_at)
        await db.commit()
        MESSAGES.labels(role=role).inc()
        serialized = await message_repo.serialize(message)
        await self.cache.append_recent_message(session_id, serialized)
        return message, serialized

    async def update_message_content(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        message_id: uuid.UUID,
        content: str,
        latency_ms: int | None,
    ) -> None:
        repo = MessageRepository(db)
        await repo.update_content(message_id, content, latency_ms)
        await db.commit()
        await self.cache.update_recent_message_content(session_id, message_id, content, latency_ms)
