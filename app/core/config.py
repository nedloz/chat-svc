from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    service_name: str = "chat-svc"

    internal_auth_mode: str = "mtls"

    app_name: str = "chat-svc"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    postgres_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/app"
    redis_url: str = "redis://redis:6379/0"

    auth_profile_url: str = "http://auth-svc:3000/api/auth/users/{user_id}/profile"
    llm_stream_url: str = "http://graph-rag-svc:8000/generate"
    llm_cancel_url: str = "http://graph-rag-svc:8000/cancel"

    user_id_header_name: str = "x-user-id"

    http_timeout_sec: float = 180.0
    redis_profile_ttl_sec: int = 3600
    redis_session_ttl_sec: int = 3600
    redis_connection_ttl_sec: int = 600
    redis_recent_messages_limit: int = 20
    redis_recent_messages_ttl_sec: int = 86400

    history_page_size_default: int = 20
    history_page_size_max: int = 50

    # Сколько последних реплик сессии уходит в graph-rag-svc вместе с вопросом.
    # Сервис ограничивает историю ещё и по объёму символов на своей стороне.
    llm_history_messages: int = 20

    stream_db_flush_every_chars: int = 120
    stream_db_flush_every_ms: int = 400


settings = Settings()
