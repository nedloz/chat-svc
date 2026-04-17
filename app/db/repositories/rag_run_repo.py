import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RagRun


class RagRunRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_rag_run(
        self,
        *,
        message_id: uuid.UUID,
        query_text: str,
        query_embedding_model: str | None = None,
        filters_json: dict[str, Any] | None = None,
        retrieved_chunk_ids: list[uuid.UUID] | None = None,
        retrieved_doc_ids: list[uuid.UUID] | None = None,
        scores_json: dict[str, Any] | None = None,
        retrieval_ms: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> RagRun:
        row = RagRun(
            message_id=message_id,
            query_text=query_text,
            query_embedding_model=query_embedding_model,
            filters_json=filters_json,
            retrieved_chunk_ids=retrieved_chunk_ids,
            retrieved_doc_ids=retrieved_doc_ids,
            scores_json=scores_json,
            retrieval_ms=retrieval_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def list_by_message_ids(self, message_ids: list[uuid.UUID]) -> list[RagRun]:
        if not message_ids:
            return []
        result = await self.db.execute(
            select(RagRun).where(RagRun.message_id.in_(message_ids)).order_by(RagRun.created_at.asc(), RagRun.id.asc())
        )
        return result.scalars().all()

    async def serialize(self, run: RagRun) -> dict[str, Any]:
        return {
            "id": str(run.id),
            "message_id": str(run.message_id),
            "query_text": run.query_text,
            "query_embedding_model": run.query_embedding_model,
            "filters_json": run.filters_json,
            "retrieved_chunk_ids": [str(item) for item in run.retrieved_chunk_ids] if run.retrieved_chunk_ids else None,
            "retrieved_doc_ids": [str(item) for item in run.retrieved_doc_ids] if run.retrieved_doc_ids else None,
            "scores_json": run.scores_json,
            "retrieval_ms": run.retrieval_ms,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "total_tokens": run.total_tokens,
            "created_at": run.created_at.isoformat() if isinstance(run.created_at, datetime) else None,
        }
