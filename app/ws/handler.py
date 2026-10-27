import logging
import uuid
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import user_context
from app.metrics import WS_CONNECTIONS_ACTIVE, WS_CONNECTIONS_TOTAL
from app.core.config import settings
from app.db.repositories.message_repo import MessageRepository
from app.db.session import SessionLocal
from app.services.auth_service import AuthService
from app.services.feedback_service import FeedbackService
from app.services.history_service import HistoryService
from app.services.internal_llm_event_service import InternalLlmEventService
from app.services.llm_service import LlmService
from app.services.message_service import MessageService
from app.services.redis_cache import RedisCacheService
from app.services.session_service import SessionService
from app.services.stream_service import StreamService
from app.ws.connection_manager import manager
from app.ws.schemas import WsCancel, WsFeedbackSet, WsHistoryGet, WsPing, WsQuestion

logger = logging.getLogger(__name__)


class ChatWebSocketHandler:
    def __init__(self) -> None:
        self.cache = RedisCacheService()
        self.auth_service = AuthService()
        self.feedback_service = FeedbackService()
        self.session_service = SessionService(self.cache)
        self.message_service = MessageService(self.cache)
        self.history_service = HistoryService(self.cache)
        self.llm_service = LlmService()
        self.stream_service = StreamService(self.message_service)
        self.internal_llm_event_service = InternalLlmEventService(self.cache, self.message_service)

    async def handle(self, websocket: WebSocket, token: str | None = None) -> None:
        try:
            resolved_user_id = self._extract_user_id(websocket, token)
        except ValueError as exc:
            logger.warning("websocket rejected: %s", exc)
            await websocket.close(code=1008)
            return
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

        WS_CONNECTIONS_TOTAL.inc()
        WS_CONNECTIONS_ACTIVE.inc()
        try:
            while True:
                payload = await websocket.receive_json()
                message_type = payload.get("type")

                # Разбор одного сообщения — под собственным try. Ошибка валидации раньше
                # выбрасывалась из цикла и закрывала WebSocket целиком: одно неверное поле
                # в одном сообщении обрывало весь чат, и на фронтенде это выглядело как
                # молчание модели. Теперь клиент получает ошибку и может продолжать.
                try:
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

                    if message_type == "cancel":
                        request = WsCancel.model_validate(payload)
                        result = await self.llm_service.cancel(request.request_id)
                        await websocket.send_json(
                            {
                                "type": "cancel_accepted",
                                "request_id": request.request_id,
                                "status": result.get("status", "cancelling"),
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
                            await self._handle_question(
                                db, websocket, resolved_user_id, session_id, profile, request_id, request.content
                            )
                        continue

                    await websocket.send_json({"type": "error", "message": f"unsupported message type: {message_type}"})
                except ValidationError as exc:
                    first = exc.errors()[0] if exc.errors() else {}
                    field = ".".join(str(x) for x in first.get("loc", ())) or "?"
                    logger.warning(
                        "invalid ws message: type=%s field=%s user_id=%s",
                        message_type, field, resolved_user_id,
                    )
                    await websocket.send_json({
                        "type": "error",
                        "message": f"некорректное сообщение ({message_type}): поле {field}",
                    })
                    continue
        except WebSocketDisconnect:
            logger.info("websocket disconnected: user_id=%s session_id=%s", resolved_user_id, session_id)
        finally:
            WS_CONNECTIONS_ACTIVE.dec()
            manager.disconnect(session_id, connection_id)
            with suppress(Exception):
                await self.cache.unregister_connection(connection_id, session_id)

    def _extract_user_id(self, websocket: WebSocket, token: str | None) -> uuid.UUID:
        _ = token  # токен клиента проверяет nginx при рукопожатии (auth_request)
        # Пользователь берётся из подписанного контекста, который выпустил auth-svc для chat-svc
        # (app/user_context.py), а не из голого заголовка: тот мог выставить любой контейнер сети.
        signed = websocket.headers.get(user_context.HEADER)
        if signed:
            try:
                return uuid.UUID(user_context.verify_from_env(signed).user_id)
            except user_context.UserContextError as exc:
                raise ValueError(f"Invalid user context: {exc}") from exc
        # Переходный режим (USER_CONTEXT_ALLOW_LEGACY): прежний заголовок без подписи.
        legacy = websocket.headers.get(settings.user_id_header_name)
        if legacy and user_context.allow_legacy():
            return uuid.UUID(str(legacy))
        raise ValueError(
            f"Missing {user_context.HEADER}. Ensure nginx validates ?token=... and forwards the signed user context."
        )

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
        # История собирается ДО создания новых сообщений, чтобы в неё не попал текущий вопрос
        # и пустая заготовка ответа. Без истории graph-rag-svc не может разрешать уточняющие
        # вопросы — ни в промпте, ни при извлечении сущностей из вопроса.
        history = await self._collect_history(db, session_id)

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

        generate_payload = {
            "request_id": request_id,
            "session_id": str(session_id),
            "user_id": str(user_id),
            "question": content,
            "profile": profile,
            "history": history,
            # Ответ приходит не в HTTP-ответе, а потоком через /ws/internal/llm; поля,
            # нужные для маршрутизации этого потока, живут в delivery.
            "response_mode": "ws_bridge",
            "delivery": {
                "assistant_message_id": str(assistant_message.id),
                "message_id": str(user_message.id),
            },
        }
        generate_result = await self.llm_service.enqueue_answer(generate_payload)
        await websocket.send_json({
            "type": "question_accepted",
            "request_id": request_id,
            "assistant_message_id": str(assistant_message.id),
            "status": generate_result.get("status", "accepted"),
        })

    async def _collect_history(self, db: AsyncSession, session_id: uuid.UUID) -> list[dict]:
        """Последние реплики сессии для передачи в graph-rag-svc.

        Пустые сообщения пропускаются: заготовка ответа ассистента создаётся пустой и
        заполняется по мере стриминга, поэтому неудавшаяся генерация оставляет в истории
        пустую строку, которую незачем показывать модели. Роль `system` не передаётся —
        graph-rag-svc её и не принимает.
        """
        repo = MessageRepository(db)
        messages = await repo.list_recent(session_id, settings.llm_history_messages)
        return [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in ("user", "assistant") and (message.content or "").strip()
        ]


handler = ChatWebSocketHandler()
