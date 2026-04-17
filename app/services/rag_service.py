from typing import Any

import httpx

from app.core.config import settings
from app.core.internal_auth import get_internal_auth_headers


class RagService:
    async def retrieve(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = get_internal_auth_headers()

        async with httpx.AsyncClient(timeout=settings.http_timeout_sec) as client:
            response = await client.post(
                settings.rag_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    async def submit_periodic_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = get_internal_auth_headers()
        async with httpx.AsyncClient(timeout=settings.http_timeout_sec) as client:
            response = await client.post(
                settings.rag_periodic_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
