import httpx

from app.core.config import settings
from app.mtls import httpx_kwargs


class AuthService:
    async def get_profile(self, user_id: str) -> dict:
        url = settings.auth_profile_url.format(user_id=user_id)

        timeout = httpx.Timeout(settings.http_timeout_sec)
        async with httpx.AsyncClient(timeout=timeout, **httpx_kwargs()) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()