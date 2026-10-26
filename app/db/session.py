import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.pg_tls import create_engine


engine = create_engine(settings.postgres_url, future=True, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis_client = redis.from_url(settings.redis_url, decode_responses=True)


async def get_db_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
