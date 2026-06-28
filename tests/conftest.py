import asyncio
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import Base, get_db_session
from app.core.cache import cache_manager
from app.main import app

# Async test engine
test_engine = create_async_engine(
    settings.POSTGRES_ASYNC_URI,
    future=True,
    poolclass=NullPool,
)
TestSession = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the event loop for the test session."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    """Create database tables before tests start, and drop them after all tests complete."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Run each test in an isolated transaction.
    Transaction is automatically rolled back after the test completes.
    """
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        yield session

        await session.close()
        await transaction.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client fixture for testing endpoints."""
    # Override get_db_session with a fresh session per request so requests never
    # share the same asyncpg connection.
    async def _override_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_db
    
    # Initialize cache manager (will use in-memory fallback if Redis container is not running)
    cache_manager.init_cache()
    await cache_manager.in_memory_cache.clear()
    
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver"
    ) as ac:
        yield ac
        
    app.dependency_overrides.clear()
