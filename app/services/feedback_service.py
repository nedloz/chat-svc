import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.feedback_repo import FeedbackRepository


class FeedbackService:
    async def save_feedback(self, db: AsyncSession, *, message_id: uuid.UUID, user_id: uuid.UUID, helpful: int) -> None:
        repo = FeedbackRepository(db)
        await repo.upsert_feedback(message_id=message_id, user_id=user_id, helpful=helpful)
        await db.commit()
