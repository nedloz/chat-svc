"""Подключение к Redis по mTLS.

Redis стоит за прокси с проверкой клиентского сертификата (main/infra/mtls/redis.conf), адрес
вида rediss://<пользователь>:<пароль>@redis:6380/0. Свой сертификат сервис берёт из тех же
переменных, что и для вызовов других сервисов: SERVICE_MTLS_CERT, SERVICE_MTLS_KEY, SERVICE_MTLS_CA.

redis-py собирает контекст TLS один раз и держит в соединении. Сертификат живёт сутки и
обновляется рядом лежащим контейнером, поэтому контекст сбрасывается перед каждым новым
соединением, и после обновления переподключение берёт свежие файлы.

Модуль одинаков в auth-svc и chat-svc (разные репозитории, общего кода нет): менять в обоих.
"""

from __future__ import annotations

import os

import redis.asyncio as redis
from redis.asyncio.connection import SSLConnection


class FreshSSLConnection(SSLConnection):
    def _connection_arguments(self):
        self.ssl_context.context = None
        return super()._connection_arguments()


def make_client(url: str) -> redis.Redis:
    if not url.startswith("rediss://"):
        return redis.from_url(url, decode_responses=True)
    pool = redis.ConnectionPool.from_url(
        url,
        decode_responses=True,
        ssl_certfile=os.getenv("SERVICE_MTLS_CERT"),
        ssl_keyfile=os.getenv("SERVICE_MTLS_KEY"),
        ssl_ca_certs=os.getenv("SERVICE_MTLS_CA"),
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
    )
    pool.connection_class = FreshSSLConnection
    return redis.Redis(connection_pool=pool)
