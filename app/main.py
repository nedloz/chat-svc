from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app import user_context
from app.core.internal_auth import validate_trusted_tokens, verify_internal_websocket
from app.core.logging import configure_logging
from app.db.session import redis_client
from app.ws.handler import handler

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_trusted_tokens()
    # Без ключа проверки подписи контекст пользователя принять нельзя: сервис не стартует.
    user_context.check_configuration()
    yield
    await redis_client.aclose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/chat")
async def chat_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    await handler.handle(websocket, token)


@app.websocket("/ws/internal/llm")
async def internal_llm_ws(websocket: WebSocket) -> None:
    service_name = await verify_internal_websocket(websocket)
    if service_name is None:
        return

    await websocket.accept()
    await websocket.send_json({"type": "ready", "service": service_name or "llm"})
    try:
        while True:
            payload = await websocket.receive_json()
            await handler.internal_llm_event_service.handle_event(payload)
    except WebSocketDisconnect:
        return
