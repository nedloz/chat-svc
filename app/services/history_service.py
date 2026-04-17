import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repositories.message_repo import MessageRepository
from app.services.redis_cache import RedisCacheService


class HistoryService:
    def __init__(self, cache: RedisCacheService):
        self.cache = cache

    async def get_history_page(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        limit: int,
        before_message_id: uuid.UUID | None,
    ) -> dict:
        limit = max(1, min(limit, settings.history_page_size_max))
        repo = MessageRepository(db)

        if before_message_id is None:
            cached = await self.cache.get_recent_messages(session_id, limit)
            if len(cached) >= min(limit, settings.redis_recent_messages_limit):
                return {
                    'items': cached[-limit:],
                    'has_more': len(cached) == limit,
                    'next_before_message_id': cached[0]['id'] if cached else None,
                    'source': 'redis',
                }

            messages = await repo.list_recent(session_id, limit)
            items = [await repo.serialize(msg) for msg in messages]
            await self.cache.set_recent_messages(session_id, items[-settings.redis_recent_messages_limit :])
            has_more = len(messages) == limit
            return {
                'items': items,
                'has_more': has_more,
                'next_before_message_id': items[0]['id'] if items else None,
                'source': 'postgres',
            }

        messages = await repo.list_before_message(session_id, before_message_id, limit)
        items = [await repo.serialize(msg) for msg in messages]
        has_more = len(messages) == limit
        return {
            'items': items,
            'has_more': has_more,
            'next_before_message_id': items[0]['id'] if items else None,
            'source': 'postgres',
        }
