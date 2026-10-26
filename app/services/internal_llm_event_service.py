import logging
import time
import uuid
from typing import Any

from fastapi import WebSocket

from app.db.session import SessionLocal
from app.db.repositories.rag_run_repo import RagRunRepository
from app.services.message_service import MessageService
from app.services.redis_cache import RedisCacheService
from app.ws.connection_manager import manager

logger = logging.getLogger(__name__)

# Внутреннее завершающее событие -> тип кадра, который ждёт клиент.
_TERMINAL_CLIENT_TYPES = {
    "message_completed": "message_completed",
    "message_cancelled": "message_cancelled",
    "message_error": "error",
}


class InternalLlmEventService:
    def __init__(self, cache: RedisCacheService, message_service: MessageService):
        self.cache = cache
        self.message_service = message_service

    async def handle_event(self, event: dict[str, Any]) -> None:
        """Сбой обработки одного события не должен рвать внутренний WebSocket.

        Исключение, вылетевшее из обработчика, закрывало соединение с graph-rag-svc без
        close-кадра («no close frame received or sent»): терялось и само событие, и всё, что
        шло следом, — в первую очередь message_completed, после которого клиент считал ответ
        незавершённым. Поэтому ошибка логируется, а клиенту всё равно уходит завершающий кадр.
        """
        try:
            await self._dispatch(event)
        except Exception:
            logger.exception(
                "Не удалось обработать внутреннее событие %s: request_id=%s",
                event.get("type"),
                event.get("request_id"),
            )
            await self._notify_terminal_failure(event)

    async def _notify_terminal_failure(self, event: dict[str, Any]) -> None:
        client_type = _TERMINAL_CLIENT_TYPES.get(event.get("type"))
        if client_type is None:
            return
        try:
            session_id = uuid.UUID(event["session_id"])
            assistant_message_id = uuid.UUID(event["assistant_message_id"])
            payload: dict[str, Any] = {
                "type": client_type,
                "request_id": event.get("request_id"),
                "assistant_message_id": str(assistant_message_id),
            }
            if client_type == "error":
                payload["message"] = event.get("message") or "llm stream failed"
            await self._broadcast(session_id, payload)
            await self.cache.delete_pending_request(assistant_message_id)
        except Exception:
            logger.exception("Не удалось отправить завершающий кадр клиенту: request_id=%s", event.get("request_id"))

    async def _dispatch(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "generation_started":
            return
        if event_type == "stream_chunk":
            await self._handle_stream_chunk(event)
            return
        if event_type == "message_completed":
            await self._handle_completed(event)
            return
        if event_type == "message_cancelled":
            await self._handle_cancelled(event)
            return
        if event_type == "message_error":
            await self._handle_error(event)
            return
        logger.warning("Unsupported internal llm event: %s", event)

    async def _broadcast(self, session_id: uuid.UUID, payload: dict[str, Any]) -> None:
        for ws in list(manager.active_by_session.get(session_id, {}).values()):
            try:
                if ws.client_state.name == "CONNECTED":
                    await ws.send_json(payload)
            except Exception:
                logger.exception("Failed to forward payload to client")

    async def _handle_stream_chunk(self, event: dict[str, Any]) -> None:
        session_id = uuid.UUID(event["session_id"])
        assistant_message_id = uuid.UUID(event["assistant_message_id"])
        pending = await self.cache.get_pending_request(assistant_message_id)
        if pending is None:
            logger.warning("Pending request not found for assistant_message_id=%s", assistant_message_id)
            return
        full_text = (pending.get("full_text") or "") + (event.get("delta") or "")
        pending["full_text"] = full_text
        await self.cache.set_pending_request(assistant_message_id, pending)

        started_at = pending.get("started_at") or time.perf_counter()
        now = time.perf_counter()
        last_flush_at = pending.get("last_flush_at") or started_at
        if (len(full_text) - int(pending.get("last_flush_len") or 0) >= 120) or ((now - last_flush_at) * 1000 >= 400):
            async with SessionLocal() as db:
                latency_ms = int((now - started_at) * 1000)
                await self.message_service.update_message_content(
                    db,
                    session_id=session_id,
                    message_id=assistant_message_id,
                    content=full_text,
                    latency_ms=latency_ms,
                )
            pending["last_flush_at"] = now
            pending["last_flush_len"] = len(full_text)
            await self.cache.set_pending_request(assistant_message_id, pending)

        await self._broadcast(session_id, event)

    async def _handle_completed(self, event: dict[str, Any]) -> None:
        session_id = uuid.UUID(event["session_id"])
        assistant_message_id = uuid.UUID(event["assistant_message_id"])
        pending = await self.cache.get_pending_request(assistant_message_id) or {}
        full_text = pending.get("full_text") or ""
        started_at = pending.get("started_at") or time.perf_counter()
        latency_ms = int((time.perf_counter() - started_at) * 1000)

        async with SessionLocal() as db:
            await self.message_service.update_message_content(
                db,
                session_id=session_id,
                message_id=assistant_message_id,
                content=full_text,
                latency_ms=latency_ms,
            )
            # Текст ответа уже сохранён выше; аналитика ретривала вторична, и её сбой не должен
            # лишать клиента message_completed.
            try:
                rag_repo = RagRunRepository(db)
                await rag_repo.create_rag_run(
                    message_id=assistant_message_id,
                    query_text=pending.get("question") or "",
                    query_embedding_model=event.get("query_embedding_model"),
                    filters_json=event.get("filters_json"),
                    retrieved_chunk_ids=_parse_uuid_list(event.get("retrieved_chunk_ids")),
                    retrieved_doc_ids=_parse_uuid_list(event.get("retrieved_doc_ids")),
                    scores_json=event.get("scores_json"),
                    retrieval_ms=event.get("retrieval_ms"),
                    prompt_tokens=event.get("prompt_tokens"),
                    completion_tokens=event.get("completion_tokens"),
                    total_tokens=event.get("total_tokens"),
                )
                await db.commit()
            except Exception:
                logger.exception("Не удалось сохранить rag_run: assistant_message_id=%s", assistant_message_id)
                await db.rollback()

        await self._broadcast(session_id, {
            "type": "message_completed",
            "request_id": event.get("request_id"),
            "assistant_message_id": str(assistant_message_id),
        })
        await self.cache.delete_pending_request(assistant_message_id)

    async def _handle_cancelled(self, event: dict[str, Any]) -> None:
        """Генерацию остановил пользователь.

        Уже накопленный текст сохраняется как есть — это не ошибка и не пустой ответ,
        поэтому клиенту уходит отдельный тип события, а не `error`.
        """
        session_id = uuid.UUID(event["session_id"])
        assistant_message_id = uuid.UUID(event["assistant_message_id"])
        pending = await self.cache.get_pending_request(assistant_message_id) or {}
        full_text = pending.get("full_text") or ""
        started_at = pending.get("started_at") or time.perf_counter()
        latency_ms = int((time.perf_counter() - started_at) * 1000)

        async with SessionLocal() as db:
            await self.message_service.update_message_content(
                db,
                session_id=session_id,
                message_id=assistant_message_id,
                content=full_text,
                latency_ms=latency_ms,
            )

        await self._broadcast(session_id, {
            "type": "message_cancelled",
            "request_id": event.get("request_id"),
            "assistant_message_id": str(assistant_message_id),
        })
        await self.cache.delete_pending_request(assistant_message_id)

    async def _handle_error(self, event: dict[str, Any]) -> None:
        session_id = uuid.UUID(event["session_id"])
        assistant_message_id = uuid.UUID(event["assistant_message_id"])
        pending = await self.cache.get_pending_request(assistant_message_id) or {}
        full_text = pending.get("full_text") or ""
        started_at = pending.get("started_at") or time.perf_counter()
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        async with SessionLocal() as db:
            await self.message_service.update_message_content(
                db,
                session_id=session_id,
                message_id=assistant_message_id,
                content=full_text,
                latency_ms=latency_ms,
            )
        await self._broadcast(session_id, {
            "type": "error",
            "request_id": event.get("request_id"),
            "assistant_message_id": str(assistant_message_id),
            "message": event.get("message") or "llm stream failed",
        })
        await self.cache.delete_pending_request(assistant_message_id)


def _parse_uuid_list(value: Any) -> list[uuid.UUID] | None:
    if not value:
        return None
    return [uuid.UUID(str(item)) for item in value]
