import httpx

from app.core.config import settings
from app.core.internal_auth import get_internal_auth_headers


class AuthService:
    async def get_profile(self, user_id: str) -> dict:
        url = settings.auth_profile_url.format(user_id=user_id)

        headers = get_internal_auth_headers()

        timeout = httpx.Timeout(settings.http_timeout_sec)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()