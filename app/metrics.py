"""Метрики Prometheus: HTTP-метрики приложения и бизнес-метрики сервиса.

/metrics отдаёт только prometheus (политика прокси, infra/mtls/*.conf); наружу через шлюз его
нет. HTTP-метрики группируются по шаблону маршрута, а не по реальному пути, поэтому число
значений метки handler ограничено числом маршрутов.
"""

from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator


def setup_metrics(app: FastAPI) -> None:
    """Подключает сбор HTTP-метрик и маршрут GET /metrics."""
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_instrument_requests_inprogress=True,
        excluded_handlers=["/metrics"],
    )
    instrumentator.instrument(app).expose(app, include_in_schema=False, should_gzip=False)


WS_CONNECTIONS_ACTIVE = Gauge(
    "pochemuchnik_ws_connections_active",
    "Открытые WebSocket-соединения пользователей",
)
WS_CONNECTIONS_TOTAL = Counter(
    "pochemuchnik_ws_connections_total",
    "Установленные WebSocket-соединения пользователей",
)
MESSAGES = Counter(
    "pochemuchnik_messages_total",
    "Созданные сообщения чата по роли",
    ["role"],  # user | assistant
)
TIME_TO_FIRST_TOKEN = Histogram(
    "pochemuchnik_time_to_first_token_seconds",
    "Время от вопроса до первого фрагмента ответа",
    buckets=(0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60),
)
ANSWER_DURATION = Histogram(
    "pochemuchnik_answer_duration_seconds",
    "Полное время ответа по исходу",
    ["outcome"],  # completed | cancelled | error
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
)
GENERATION_FAILURES = Counter(
    "pochemuchnik_generation_failures_total",
    "Сбои генерации ответа по причине",
    ["reason"],  # llm_error (graph-rag-svc прислал message_error) | internal_error (сбой обработки события в chat-svc)
)
FEEDBACK = Counter(
    "pochemuchnik_feedback_total",
    "Оценки ответов",
    ["value"],  # positive | negative | neutral
)
REDIS_CACHE_REQUESTS = Counter(
    "pochemuchnik_redis_cache_requests_total",
    "Обращения к кэшу Redis",
    ["kind", "outcome"],  # kind: profile | session | recent_messages; outcome: hit | miss
)
