from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    service_name: str = "chat-svc"
    service_token: str = "change-me"

    internal_auth_enabled: bool = True
    internal_auth_header_name: str = "X-Service-Token"
    internal_service_name_header: str = "X-Service-Name"

    app_name: str = "chat-svc"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    postgres_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/app"
    redis_url: str = "redis://redis:6379/0"

    auth_profile_url: str = "http://auth-svc:3000/api/auth/users/{user_id}/profile"
    rag_url: str = "http://rag-svc:3245/api/rag/query"
    rag_periodic_url: str = "http://rag-svc:3245/api/rag/query"
    llm_stream_url: str = "http://llm-svc:4000/generate"

    trusted_service_tokens: str = "{}"

    user_id_header_name: str = "x-user-id"

    http_timeout_sec: float = 180.0
    redis_profile_ttl_sec: int = 3600
    redis_session_ttl_sec: int = 3600
    redis_connection_ttl_sec: int = 600
    redis_recent_messages_limit: int = 20
    redis_recent_messages_ttl_sec: int = 86400
    rag_periodic_interval_days: int = 20
    rag_periodic_messages_limit: int = 10

    history_page_size_default: int = 20
    history_page_size_max: int = 50

    stream_db_flush_every_chars: int = 120
    stream_db_flush_every_ms: int = 400


settings = Settings()
