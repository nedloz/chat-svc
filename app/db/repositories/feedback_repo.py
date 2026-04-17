import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback


class FeedbackRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_feedback(self, *, message_id: uuid.UUID, user_id: uuid.UUID, helpful: int) -> Feedback:
        result = await self.db.execute(
            select(Feedback).where(Feedback.message_id == message_id, Feedback.user_id == user_id).limit(1)
        )
        feedback = result.scalar_one_or_none()
        if feedback:
            feedback.helpful = helpful
            await self.db.flush()
            await self.db.refresh(feedback)
            return feedback

        feedback = Feedback(message_id=message_id, user_id=user_id, helpful=helpful)
        self.db.add(feedback)
        await self.db.flush()
        await self.db.refresh(feedback)
        return feedback
