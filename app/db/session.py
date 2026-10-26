from app.redis_tls import make_client
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.pg_tls import create_engine


engine = create_engine(settings.postgres_url, future=True, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis_client = make_client(settings.redis_url)


async def get_db_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
