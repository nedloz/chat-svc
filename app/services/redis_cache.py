import json
import uuid
from typing import Any

from app.metrics import REDIS_CACHE_REQUESTS
from app.core.config import settings
from app.db.session import redis_client


def profile_key(user_id: uuid.UUID) -> str:
    return f'ws:user:{user_id}:profile'


def session_key(user_id: uuid.UUID) -> str:
    return f'ws:user:{user_id}:session'


def conn_key(connection_id: str) -> str:
    return f'ws:conn:{connection_id}'


def session_connections_key(session_id: uuid.UUID) -> str:
    return f'ws:session:{session_id}:connections'


def recent_messages_key(session_id: uuid.UUID) -> str:
    return f'chat:session:{session_id}:recent_messages'


def pending_request_key(assistant_message_id: uuid.UUID) -> str:
    return f'chat:pending:{assistant_message_id}'


class RedisCacheService:
    async def cache_profile(self, user_id: uuid.UUID, profile: dict[str, Any]) -> None:
        await redis_client.setex(profile_key(user_id), settings.redis_profile_ttl_sec, json.dumps(profile))

    async def get_profile(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        raw = await redis_client.get(profile_key(user_id))
        REDIS_CACHE_REQUESTS.labels(kind="profile", outcome="hit" if raw else "miss").inc()
        return json.loads(raw) if raw else None

    async def cache_session_id(self, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        await redis_client.setex(session_key(user_id), settings.redis_session_ttl_sec, str(session_id))

    async def get_session_id(self, user_id: uuid.UUID) -> uuid.UUID | None:
        raw = await redis_client.get(session_key(user_id))
        REDIS_CACHE_REQUESTS.labels(kind="session", outcome="hit" if raw else "miss").inc()
        return uuid.UUID(raw) if raw else None

    async def register_connection(self, connection_id: str, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        payload = json.dumps({'user_id': str(user_id), 'session_id': str(session_id)})
        await redis_client.setex(conn_key(connection_id), settings.redis_connection_ttl_sec, payload)
        await redis_client.sadd(session_connections_key(session_id), connection_id)
        await redis_client.expire(session_connections_key(session_id), settings.redis_connection_ttl_sec)

    async def unregister_connection(self, connection_id: str, session_id: uuid.UUID) -> None:
        await redis_client.delete(conn_key(connection_id))
        await redis_client.srem(session_connections_key(session_id), connection_id)

    async def set_recent_messages(self, session_id: uuid.UUID, messages: list[dict[str, Any]]) -> None:
        key = recent_messages_key(session_id)
        await redis_client.delete(key)
        if messages:
            await redis_client.rpush(key, *[json.dumps(item) for item in messages])
        await redis_client.ltrim(key, -settings.redis_recent_messages_limit, -1)
        await redis_client.expire(key, settings.redis_recent_messages_ttl_sec)

    async def get_recent_messages(self, session_id: uuid.UUID, limit: int) -> list[dict[str, Any]]:
        key = recent_messages_key(session_id)
        raw_items = await redis_client.lrange(key, max(0, -limit), -1)
        REDIS_CACHE_REQUESTS.labels(kind="recent_messages", outcome="hit" if raw_items else "miss").inc()
        return [json.loads(item) for item in raw_items]

    async def append_recent_message(self, session_id: uuid.UUID, message: dict[str, Any]) -> None:
        key = recent_messages_key(session_id)
        await redis_client.rpush(key, json.dumps(message))
        await redis_client.ltrim(key, -settings.redis_recent_messages_limit, -1)
        await redis_client.expire(key, settings.redis_recent_messages_ttl_sec)

    async def update_recent_message_content(self, session_id: uuid.UUID, message_id: uuid.UUID, content: str, latency_ms: int | None) -> None:
        key = recent_messages_key(session_id)
        items = await redis_client.lrange(key, 0, -1)
        if not items:
            return
        patched: list[str] = []
        changed = False
        for raw in items:
            item = json.loads(raw)
            if item.get('id') == str(message_id):
                item['content'] = content
                item['latency_ms'] = latency_ms
                changed = True
            patched.append(json.dumps(item))
        if changed:
            await redis_client.delete(key)
            if patched:
                await redis_client.rpush(key, *patched)
            await redis_client.expire(key, settings.redis_recent_messages_ttl_sec)

    async def set_pending_request(self, assistant_message_id: uuid.UUID, payload: dict[str, Any], ttl_sec: int = 3600) -> None:
        await redis_client.setex(pending_request_key(assistant_message_id), ttl_sec, json.dumps(payload))

    async def get_pending_request(self, assistant_message_id: uuid.UUID) -> dict[str, Any] | None:
        raw = await redis_client.get(pending_request_key(assistant_message_id))
        return json.loads(raw) if raw else None

    async def delete_pending_request(self, assistant_message_id: uuid.UUID) -> None:
        await redis_client.delete(pending_request_key(assistant_message_id))
