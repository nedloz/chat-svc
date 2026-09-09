from typing import Any

import httpx

from app.core.config import settings
from app.core.internal_auth import get_internal_auth_headers


class LlmService:
    async def cancel(self, request_id: str) -> dict[str, Any]:
        """Просит graph-rag-svc остановить генерацию.

        404 означает, что задание уже завершилось, — для пользователя это не ошибка,
        поэтому наверх поднимается как обычный результат, а не исключение.
        """
        headers = get_internal_auth_headers()
        async with httpx.AsyncClient(timeout=settings.http_timeout_sec) as client:
            response = await client.post(settings.llm_cancel_url, json={"request_id": request_id}, headers=headers)
            if response.status_code == 404:
                return {"status": "not_found"}
            response.raise_for_status()
            return response.json()

    async def enqueue_answer(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = get_internal_auth_headers()
        async with httpx.AsyncClient(timeout=settings.http_timeout_sec) as client:
            response = await client.post(settings.llm_stream_url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
