import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatSession


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_user_id(self, user_id: uuid.UUID) -> ChatSession | None:
        result = await self.db.execute(
            select(ChatSession).where(ChatSession.user_id == user_id, ChatSession.is_archived.is_(False)).limit(1)
        )
        return result.scalar_one_or_none()

    async def create_for_user(self, user_id: uuid.UUID) -> ChatSession:
        session = ChatSession(user_id=user_id)
        self.db.add(session)
        await self.db.flush()
        await self.db.refresh(session)
        return session

    async def get_or_create_for_user(self, user_id: uuid.UUID) -> ChatSession:
        session = await self.get_by_user_id(user_id)
        if session:
            return session
        return await self.create_for_user(user_id)

    async def update_last_message(self, session_id: uuid.UUID, message_id: uuid.UUID, created_at: datetime) -> None:
        session = await self.db.get(ChatSession, session_id)
        if session is None:
            return
        session.last_message_id = message_id
        session.last_message_at = created_at
        session.updated_at = datetime.utcnow()
        await self.db.flush()
