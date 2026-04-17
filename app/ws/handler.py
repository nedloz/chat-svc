import logging
import uuid
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repositories.rag_run_repo import RagRunRepository
from app.db.session import SessionLocal
from app.services.auth_service import AuthService
from app.services.feedback_service import FeedbackService
from app.services.history_service import HistoryService
from app.services.internal_llm_event_service import InternalLlmEventService
from app.services.llm_service import LlmService
from app.services.message_service import MessageService
from app.services.rag_service import RagService
from app.services.redis_cache import RedisCacheService
from app.services.session_service import SessionService
from app.services.stream_service import StreamService
from app.ws.connection_manager import manager
from app.ws.schemas import WsFeedbackSet, WsHistoryGet, WsPing, WsQuestion

logger = logging.getLogger(__name__)


class ChatWebSocketHandler:
    def __init__(self) -> None:
        self.cache = RedisCacheService()
        self.auth_service = AuthService()
        self.feedback_service = FeedbackService()
        self.session_service = SessionService(self.cache)
        self.message_service = MessageService(self.cache)
        self.history_service = HistoryService(self.cache)
        self.rag_service = RagService()
        self.llm_service = LlmService()
        self.stream_service = StreamService(self.message_service)
        self.internal_llm_event_service = InternalLlmEventService(self.cache, self.message_service)

    async def handle(self, websocket: WebSocket, token: str | None = None) -> None:
        resolved_user_id = self._extract_user_id(websocket, token)
        profile = await self.cache.get_profile(resolved_user_id)
        if profile is None:
            profile = await self.auth_service.get_profile(resolved_user_id)
            await self.cache.cache_profile(resolved_user_id, profile)

        async with SessionLocal() as db:
            session_id = await self.session_service.get_or_create_session_id(db, resolved_user_id)

        connection_id = str(uuid.uuid4())
        await manager.connect(websocket, session_id, connection_id)
        await self.cache.register_connection(connection_id, resolved_user_id, session_id)

        await websocket.send_json(
            {
                "type": "connected",
                "user_id": str(resolved_user_id),
                "session_id": str(session_id),
            }
        )

        try:
            while True:
                payload = await websocket.receive_json()
                message_type = payload.get("type")

                if message_type == "ping":
                    WsPing.model_validate(payload)
                    await websocket.send_json({"type": "pong"})
                    continue

                if message_type == "history_get":
                    request = WsHistoryGet.model_validate(payload)
                    async with SessionLocal() as db:
                        page = await self.history_service.get_history_page(
                            db,
                            session_id=session_id,
                            limit=request.limit,
                            before_message_id=request.before_message_id,
                        )
                    await websocket.send_json(
                        {
                            "type": "history_page",
                            "session_id": str(session_id),
                            "items": page["items"],
                            "has_more": page["has_more"],
                            "next_before_message_id": page["next_before_message_id"],
                            "source": page["source"],
                        }
                    )
                    continue

                if message_type == "feedback_set":
                    request = WsFeedbackSet.model_validate(payload)
                    async with SessionLocal() as db:
                        await self.feedback_service.save_feedback(
                            db,
                            message_id=request.message_id,
                            user_id=resolved_user_id,
                            helpful=request.helpful,
                        )
                    await websocket.send_json(
                        {
                            "type": "feedback_saved",
                            "message_id": str(request.message_id),
                            "helpful": request.helpful,
                        }
                    )
                    continue

                if message_type == "question":
                    request = WsQuestion.model_validate(payload)
                    request_id = str(request.request_id or uuid.uuid4())
                    profile = await self.cache.get_profile(resolved_user_id)
                    if profile is None:
                        profile = await self.auth_service.get_profile(resolved_user_id)
                        await self.cache.cache_profile(resolved_user_id, profile)
                    async with SessionLocal() as db:
                        try:
                            await self._maybe_submit_periodic_history(
                                db=db,
                                user_id=resolved_user_id,
                                session_id=session_id,
                                profile=profile,
                            )
                        except Exception:
                            logger.exception("Periodic rag history request failed for session_id=%s", session_id)
                        await self._handle_question(
                            db, websocket, resolved_user_id, session_id, profile, request_id, request.content
                        )
                    continue

                await websocket.send_json({"type": "error", "message": f"unsupported message type: {message_type}"})
        except WebSocketDisconnect:
            logger.info("websocket disconnected: user_id=%s session_id=%s", resolved_user_id, session_id)
        finally:
            manager.disconnect(session_id, connection_id)
            with suppress(Exception):
                await self.cache.unregister_connection(connection_id, session_id)

    def _extract_user_id(self, websocket: WebSocket, token: str | None) -> uuid.UUID:
        _ = token  # token is validated by nginx during websocket handshake
        candidate = websocket.headers.get(settings.user_id_header_name)
        if not candidate:
            raise ValueError(
                f"Missing user id in forwarded header {settings.user_id_header_name}. "
                "Ensure nginx validates ?token=... and injects user id header."
            )
        return uuid.UUID(str(candidate))

    async def _handle_question(
        self,
        db: AsyncSession,
        websocket: WebSocket,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        profile: dict,
        request_id: str,
        content: str,
    ) -> None:
        user_message, _ = await self.message_service.create_message(
            db,
            session_id=session_id,
            user_id=user_id,
            role="user",
            content=content,
        )
        assistant_message, _ = await self.message_service.create_message(
            db,
            session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="",
            reply_to_message_id=user_message.id,
        )

        await websocket.send_json(
            {
                "type": "message_started",
                "request_id": request_id,
                "session_id": str(session_id),
                "user_message_id": str(user_message.id),
                "assistant_message_id": str(assistant_message.id),
            }
        )

        pending_payload = {
            "request_id": request_id,
            "session_id": str(session_id),
            "assistant_message_id": str(assistant_message.id),
            "user_id": str(user_id),
            "message_id": str(user_message.id),
            "question": content,
            "started_at": __import__("time").perf_counter(),
            "last_flush_at": __import__("time").perf_counter(),
            "last_flush_len": 0,
            "full_text": "",
        }
        await self.cache.set_pending_request(assistant_message.id, pending_payload)

        rag_payload = {
            "request_id": request_id,
            "message_id": str(user_message.id),
            "assistant_message_id": str(assistant_message.id),
            "session_id": str(session_id),
            "user_id": str(user_id),
            "question": content,
            "profile": profile,
        }
        rag_result = await self.rag_service.retrieve(rag_payload)
        await websocket.send_json({
            "type": "question_accepted",
            "request_id": request_id,
            "assistant_message_id": str(assistant_message.id),
            "status": rag_result.get("status", "accepted"),
        })

    async def _maybe_submit_periodic_history(
        self,
        *,
        db: AsyncSession,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        profile: dict,
    ) -> None:
        if settings.rag_periodic_interval_days <= 0:
            return
        should_run = await self.cache.try_acquire_periodic_rag_gate(session_id, settings.rag_periodic_interval_days)
        if not should_run:
            return

        history = await self.history_service.get_history_page(
            db,
            session_id=session_id,
            limit=settings.rag_periodic_messages_limit,
            before_message_id=None,
        )
        messages = history.get("items") or []
        if not messages:
            await self.cache.clear_periodic_rag_gate(session_id)
            return

        message_ids = [uuid.UUID(item["id"]) for item in messages if item.get("id")]
        rag_repo = RagRunRepository(db)
        rag_runs = await rag_repo.list_by_message_ids(message_ids)
        serialized_runs = [await rag_repo.serialize(run) for run in rag_runs]

        anchor_message_id = uuid.UUID(messages[-1]["id"])
        request_id = str(uuid.uuid4())
        assistant_message, _ = await self.message_service.create_message(
            db,
            session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="",
            reply_to_message_id=anchor_message_id,
        )

        marker_question = "[periodic-session-history-sync]"
        pending_payload = {
            "request_id": request_id,
            "session_id": str(session_id),
            "assistant_message_id": str(assistant_message.id),
            "user_id": str(user_id),
            "message_id": str(anchor_message_id),
            "question": marker_question,
            "started_at": __import__("time").perf_counter(),
            "last_flush_at": __import__("time").perf_counter(),
            "last_flush_len": 0,
            "full_text": "",
        }
        await self.cache.set_pending_request(assistant_message.id, pending_payload)

        rag_payload = {
            "type": "periodic_session_history",
            "request_id": request_id,
            "message_id": str(anchor_message_id),
            "assistant_message_id": str(assistant_message.id),
            "session_id": str(session_id),
            "user_id": str(user_id),
            "question": marker_question,
            "profile": profile,
            "messages": messages,
            "rag_runs": serialized_runs,
            "interval_days": settings.rag_periodic_interval_days,
        }
        try:
            rag_result = await self.rag_service.submit_periodic_history(rag_payload)
        except Exception:
            await self.cache.delete_pending_request(assistant_message.id)
            await self.cache.clear_periodic_rag_gate(session_id)
            raise
        logger.info(
            "Periodic rag history submitted: session_id=%s assistant_message_id=%s status=%s",
            session_id,
            assistant_message.id,
            rag_result.get("status"),
        )


handler = ChatWebSocketHandler()
