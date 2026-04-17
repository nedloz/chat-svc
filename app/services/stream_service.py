import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repositories.rag_run_repo import RagRunRepository
from app.services.message_service import MessageService


class StreamService:
    def __init__(self, message_service: MessageService):
        self.message_service = message_service

    async def consume_stream(
        self,
        *,
        db: AsyncSession,
        session_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        stream,
        websocket,
        request_id: str,
        question: str,
        rag_meta: dict[str, Any] | None = None,
    ) -> None:
        full_text = ""
        started_at = time.perf_counter()
        last_flush_at = time.perf_counter()
        rag_meta = rag_meta or {}

        try:
            async for event in stream:
                event_type = event.get("type", "token")

                if event_type in {"token", "chunk"}:
                    delta = event.get("delta") or event.get("content") or ""
                    if not delta:
                        continue
                    full_text += delta
                    await websocket.send_json(
                        {
                            "type": "stream_chunk",
                            "request_id": request_id,
                            "assistant_message_id": str(assistant_message_id),
                            "delta": delta,
                        }
                    )
                    should_flush = (
                        len(full_text) % settings.stream_db_flush_every_chars < len(delta)
                        or (time.perf_counter() - last_flush_at) * 1000 >= settings.stream_db_flush_every_ms
                    )
                    if should_flush:
                        latency_ms = int((time.perf_counter() - started_at) * 1000)
                        await self.message_service.update_message_content(
                            db,
                            session_id=session_id,
                            message_id=assistant_message_id,
                            content=full_text,
                            latency_ms=latency_ms,
                        )
                        last_flush_at = time.perf_counter()

                elif event_type == "rag_meta":
                    rag_meta = {**rag_meta, **event}
                elif event_type == "done":
                    rag_meta = {**rag_meta, **event}
                elif event_type == "error":
                    raise RuntimeError(event.get("message", "llm stream failed"))

            latency_ms = int((time.perf_counter() - started_at) * 1000)
            await self.message_service.update_message_content(
                db,
                session_id=session_id,
                message_id=assistant_message_id,
                content=full_text,
                latency_ms=latency_ms,
            )
            rag_repo = RagRunRepository(db)
            await rag_repo.create_rag_run(
                message_id=assistant_message_id,
                query_text=question,
                query_embedding_model=rag_meta.get("query_embedding_model"),
                filters_json=rag_meta.get("filters_json"),
                retrieved_chunk_ids=rag_meta.get("retrieved_chunk_ids"),
                retrieved_doc_ids=rag_meta.get("retrieved_doc_ids"),
                scores_json=rag_meta.get("scores_json"),
                retrieval_ms=rag_meta.get("retrieval_ms"),
                prompt_tokens=rag_meta.get("prompt_tokens"),
                completion_tokens=rag_meta.get("completion_tokens"),
                total_tokens=rag_meta.get("total_tokens"),
            )
            await db.commit()
            await websocket.send_json(
                {
                    "type": "message_completed",
                    "request_id": request_id,
                    "assistant_message_id": str(assistant_message_id),
                }
            )
        except Exception:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            await self.message_service.update_message_content(
                db,
                session_id=session_id,
                message_id=assistant_message_id,
                content=full_text,
                latency_ms=latency_ms,
            )
            raise
