from typing import Any

import httpx

from app.core.config import settings
from app.core.internal_auth import get_internal_auth_headers


class LlmService:
    async def enqueue_answer(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = get_internal_auth_headers()
        async with httpx.AsyncClient(timeout=settings.http_timeout_sec) as client:
            response = await client.post(settings.llm_stream_url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
