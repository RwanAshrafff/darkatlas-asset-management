from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Create async database engine
engine = create_async_engine(
    settings.POSTGRES_ASYNC_URI,
    echo=False,  # Set to True for SQL logging in development
    future=True,
)

# Async session factory
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database session to FastAPI endpoints."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
