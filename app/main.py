from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import engine, Base
from app.core.cache import cache_manager
from app.api import auth, assets, relationships


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup: Initialize cache connections
    cache_manager.init_cache()

    # 2. Startup: Auto-create tables in database if they do not exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    # 3. Shutdown: Close cache client connections
    if cache_manager.redis_cache.client:
        try:
            await cache_manager.redis_cache.client.close()
        except Exception:
            pass


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="DarkAtlas Attack Surface Monitoring (ASM) Asset Management System REST API",
    version="1.0.0",
    lifespan=lifespan,
)

# Set CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(
    auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"]
)
app.include_router(
    assets.router, prefix=f"{settings.API_V1_STR}/assets", tags=["Assets"]
)
app.include_router(
    relationships.router, prefix=settings.API_V1_STR, tags=["Relationships"]
)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} REST API",
        "docs_url": "/docs",
        "status": "healthy",
    }
