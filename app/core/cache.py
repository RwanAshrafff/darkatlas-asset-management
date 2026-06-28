import json
import logging
from typing import Optional, Any
from uuid import UUID
import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger("darkatlas.cache")


class InMemoryCache:
    """Fallback cache using python dictionary when Redis is not reachable or during tests."""

    def __init__(self):
        self._cache = {}
        # Simple expiration tracking
        self._expires = {}

    async def get(self, key: str) -> Optional[str]:
        # Simple check for expiration (since this is fallback, keep it basic)
        import time

        now = time.time()
        if key in self._expires and self._expires[key] < now:
            await self.delete(key)
            return None
        return self._cache.get(key)

    async def set(self, key: str, value: str, expire: int = 300) -> None:
        import time

        self._cache[key] = value
        self._expires[key] = time.time() + expire

    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)
        self._expires.pop(key, None)

    async def clear(self) -> None:
        self._cache.clear()
        self._expires.clear()


class RedisCache:
    """Cache implementation using Redis."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client: Optional[aioredis.Redis] = None
        self._failed = False

    def connect(self):
        try:
            self.client = aioredis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
            self._failed = False
        except Exception as e:
            logger.warning(f"Failed to connect to Redis at {self.redis_url}: {e}")
            self.client = None
            self._failed = True

    async def get(self, key: str) -> Optional[str]:
        if not self.client or self._failed:
            return None
        try:
            return await self.client.get(key)
        except Exception as e:
            logger.debug(f"Redis get failed: {e}")
            return None

    async def set(self, key: str, value: str, expire: int = 300) -> None:
        if not self.client or self._failed:
            return
        try:
            await self.client.set(key, value, ex=expire)
        except Exception as e:
            logger.debug(f"Redis set failed: {e}")

    async def delete(self, key: str) -> None:
        if not self.client or self._failed:
            return
        try:
            await self.client.delete(key)
        except Exception as e:
            logger.debug(f"Redis delete failed: {e}")

    async def clear_pattern(self, pattern: str) -> None:
        if not self.client or self._failed:
            return
        try:
            keys = await self.client.keys(pattern)
            if keys:
                await self.client.delete(*keys)
        except Exception as e:
            logger.debug(f"Redis clear pattern failed: {e}")


class CacheManager:
    def __init__(self):
        self.redis_cache = RedisCache(settings.REDIS_URI)
        self.in_memory_cache = InMemoryCache()
        self._redis_active = None

    def init_cache(self) -> None:
        """Initialize connections."""
        self.redis_cache.connect()

    async def _is_redis_working(self) -> bool:
        if self.redis_cache.client is None:
            return False
        try:
            # Quick ping to check status
            await self.redis_cache.client.ping()
            return True
        except Exception:
            return False

    async def get_json(self, key: str) -> Optional[Any]:
        """Retrieve a JSON deserialized value from cache."""
        val = None
        if await self._is_redis_working():
            val = await self.redis_cache.get(key)
        else:
            val = await self.in_memory_cache.get(key)

        if val is not None:
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                return None
        return None

    async def set_json(self, key: str, value: Any, expire: int = 300) -> None:
        """Store a JSON serialized value in cache."""
        val_str = json.dumps(value)
        if await self._is_redis_working():
            await self.redis_cache.set(key, val_str, expire=expire)
        else:
            await self.in_memory_cache.set(key, val_str, expire=expire)

    async def delete(self, key: str) -> None:
        """Delete a key from the cache."""
        if await self._is_redis_working():
            await self.redis_cache.delete(key)
        else:
            await self.in_memory_cache.delete(key)

    async def clear_tenant_cache(self, tenant_id: UUID) -> None:
        """Clear all cache keys matching the tenant prefix to prevent stale reads after writes."""
        prefix = f"tenant:{tenant_id}:"
        if await self._is_redis_working():
            await self.redis_cache.clear_pattern(f"{prefix}*")

        # Clear in-memory keys
        keys_to_del = [
            k for k in self.in_memory_cache._cache.keys() if k.startswith(prefix)
        ]
        for k in keys_to_del:
            await self.in_memory_cache.delete(k)


cache_manager = CacheManager()
