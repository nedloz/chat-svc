import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from prometheus_client import REGISTRY

from app.services import feedback_service, internal_llm_event_service, message_service, redis_cache
from app.services.internal_llm_event_service import InternalLlmEventService
from app.ws import handler as ws_handler


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class FakeRedis:
    def __init__(self, values=None, lists=None):
        self.values = values or {}
        self.lists = lists or {}

    async def get(self, key):
        return self.values.get(key)

    async def lrange(self, key, _start, _end):
        return self.lists.get(key, [])


def test_profile_cache_counts_hit_and_miss(monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(redis_cache, "redis_client", FakeRedis({redis_cache.profile_key(user_id): '{"a": 1}'}))
    cache = redis_cache.RedisCacheService()
    hit = sample("pochemuchnik_redis_cache_requests_total", kind="profile", outcome="hit")
    miss = sample("pochemuchnik_redis_cache_requests_total", kind="profile", outcome="miss")

    assert asyncio.run(cache.get_profile(user_id)) == {"a": 1}
    assert asyncio.run(cache.get_profile(uuid.uuid4())) is None

    assert sample("pochemuchnik_redis_cache_requests_total", kind="profile", outcome="hit") == hit + 1
    assert sample("pochemuchnik_redis_cache_requests_total", kind="profile", outcome="miss") == miss + 1


def test_empty_recent_messages_count_as_miss(monkeypatch):
    monkeypatch.setattr(redis_cache, "redis_client", FakeRedis())
    before = sample("pochemuchnik_redis_cache_requests_total", kind="recent_messages", outcome="miss")
    asyncio.run(redis_cache.RedisCacheService().get_recent_messages(uuid.uuid4(), 10))
    assert sample("pochemuchnik_redis_cache_requests_total", kind="recent_messages", outcome="miss") == before + 1


class FakeDb:
    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.mark.parametrize("helpful,value", [(1, "positive"), (-1, "negative"), (0, "neutral")])
def test_feedback_is_counted_by_sign(monkeypatch, helpful, value):
    class Repo:
        def __init__(self, _db):
            pass

        async def upsert_feedback(self, **_kwargs):
            pass

    monkeypatch.setattr(feedback_service, "FeedbackRepository", Repo)
    before = sample("pochemuchnik_feedback_total", value=value)
    asyncio.run(feedback_service.FeedbackService().save_feedback(
        FakeDb(), message_id=uuid.uuid4(), user_id=uuid.uuid4(), helpful=helpful))
    assert sample("pochemuchnik_feedback_total", value=value) == before + 1


def test_created_messages_are_counted_by_role(monkeypatch):
    class MessageRepo:
        def __init__(self, _db):
            pass

        async def create_message(self, **kwargs):
            return SimpleNamespace(id=uuid.uuid4(), created_at=None)

        async def serialize(self, message):
            return {"id": str(message.id)}

    class SessionRepo:
        def __init__(self, _db):
            pass

        async def update_last_message(self, *_args):
            pass

    class Cache:
        async def append_recent_message(self, *_args):
            pass

    monkeypatch.setattr(message_service, "MessageRepository", MessageRepo)
    monkeypatch.setattr(message_service, "SessionRepository", SessionRepo)
    service = message_service.MessageService(Cache())
    before = sample("pochemuchnik_messages_total", role="assistant")
    asyncio.run(service.create_message(
        FakeDb(), session_id=uuid.uuid4(), user_id=uuid.uuid4(), role="assistant", content=""))
    assert sample("pochemuchnik_messages_total", role="assistant") == before + 1


class PendingCache:
    def __init__(self, pending):
        self.pending = pending

    async def get_pending_request(self, _message_id):
        return self.pending

    async def set_pending_request(self, _message_id, payload):
        self.pending = payload

    async def delete_pending_request(self, _message_id):
        self.pending = None


class FakeMessages:
    async def update_message_content(self, *_args, **_kwargs):
        pass


@asynccontextmanager
async def fake_session_local():
    yield FakeDb()


def llm_event(event_type, **extra):
    return {"type": event_type, "session_id": str(uuid.uuid4()),
            "assistant_message_id": str(uuid.uuid4()), "request_id": "r1", **extra}


def make_events(monkeypatch, pending):
    monkeypatch.setattr(internal_llm_event_service, "SessionLocal", fake_session_local)
    return InternalLlmEventService(PendingCache(pending), FakeMessages())


def test_only_the_first_chunk_observes_time_to_first_token(monkeypatch):
    service = make_events(monkeypatch, {"full_text": "", "started_at": 0.0})
    before = sample("pochemuchnik_time_to_first_token_seconds_count")
    asyncio.run(service.handle_event(llm_event("stream_chunk", delta="При")))
    asyncio.run(service.handle_event(llm_event("stream_chunk", delta="вет")))
    assert sample("pochemuchnik_time_to_first_token_seconds_count") == before + 1


@pytest.mark.parametrize("event_type,outcome", [
    ("message_completed", "completed"), ("message_cancelled", "cancelled"), ("message_error", "error"),
])
def test_answer_duration_is_recorded_by_outcome(monkeypatch, event_type, outcome):
    service = make_events(monkeypatch, {"full_text": "x", "started_at": 0.0})
    before = sample("pochemuchnik_answer_duration_seconds_count", outcome=outcome)
    asyncio.run(service.handle_event(llm_event(event_type)))
    assert sample("pochemuchnik_answer_duration_seconds_count", outcome=outcome) == before + 1


def test_message_error_event_counts_llm_error_failure(monkeypatch):
    service = make_events(monkeypatch, {"full_text": "", "started_at": 0.0})
    before = sample("pochemuchnik_generation_failures_total", reason="llm_error")
    asyncio.run(service.handle_event(llm_event("message_error", message="любой текст")))
    assert sample("pochemuchnik_generation_failures_total", reason="llm_error") == before + 1


def test_event_handler_crash_counts_internal_error_and_does_not_raise(monkeypatch):
    service = make_events(monkeypatch, {})
    before = sample("pochemuchnik_generation_failures_total", reason="internal_error")
    asyncio.run(service.handle_event({"type": "stream_chunk"}))  # нет session_id: KeyError внутри
    assert sample("pochemuchnik_generation_failures_total", reason="internal_error") == before + 1


class FakeSocket:
    async def send_json(self, _payload):
        pass

    async def receive_json(self):
        raise WebSocketDisconnect()


def test_ws_connection_gauge_returns_to_zero_after_disconnect(monkeypatch):
    handler = ws_handler.ChatWebSocketHandler()

    class Cache:
        async def get_profile(self, _uid):
            return {}

        async def register_connection(self, *_args):
            pass

        async def unregister_connection(self, *_args):
            pass

    async def session_id(*_args):
        return uuid.uuid4()

    async def connect(*_args):
        pass

    handler.cache = Cache()
    handler.session_service = SimpleNamespace(get_or_create_session_id=session_id)
    monkeypatch.setattr(handler, "_extract_user_id", lambda *_a: uuid.uuid4())
    monkeypatch.setattr(ws_handler, "SessionLocal", fake_session_local)
    monkeypatch.setattr(ws_handler.manager, "connect", connect)
    monkeypatch.setattr(ws_handler.manager, "disconnect", lambda *_a: None)
    active = sample("pochemuchnik_ws_connections_active")
    total = sample("pochemuchnik_ws_connections_total")

    asyncio.run(handler.handle(FakeSocket(), "token"))

    assert sample("pochemuchnik_ws_connections_active") == active
    assert sample("pochemuchnik_ws_connections_total") == total + 1
