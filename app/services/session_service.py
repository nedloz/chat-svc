import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.session_repo import SessionRepository
from app.services.redis_cache import RedisCacheService


class SessionService:
    def __init__(self, cache: RedisCacheService):
        self.cache = cache

    async def get_or_create_session_id(self, db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
        cached = await self.cache.get_session_id(user_id)
        if cached:
            return cached

        repo = SessionRepository(db)
        session = await repo.get_or_create_for_user(user_id)
        await self.cache.cache_session_id(user_id, session.id)
        await db.commit()
        return session.id
