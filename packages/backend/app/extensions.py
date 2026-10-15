import fnmatch
import time
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
import redis
from .config import Settings


db = SQLAlchemy()
jwt = JWTManager()


class ResilientRedis:
    """Redis client wrapper that falls back to in-memory storage when unreachable."""

    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._fallback_active = False
        self._store: dict[str, tuple[str, float | None]] = {}

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            k for k, (_, exp) in self._store.items() if exp is not None and exp <= now
        ]
        for k in expired:
            self._store.pop(k, None)

    def get(self, key: str) -> str | None:
        if not self._fallback_active:
            try:
                return self._client.get(key)
            except (redis.exceptions.RedisError, OSError):
                self._fallback_active = True
        self._cleanup_expired()
        item = self._store.get(key)
        return item[0] if item else None

    def set(self, key: str, value: str) -> bool:
        if not self._fallback_active:
            try:
                return bool(self._client.set(key, value))
            except (redis.exceptions.RedisError, OSError):
                self._fallback_active = True
        self._store[key] = (str(value), None)
        return True

    def setex(self, key: str, ttl_seconds: int, value: str) -> bool:
        if not self._fallback_active:
            try:
                return bool(self._client.setex(key, int(ttl_seconds), value))
            except (redis.exceptions.RedisError, OSError):
                self._fallback_active = True
        self._store[key] = (str(value), time.time() + int(ttl_seconds))
        return True

    def delete(self, *keys: str) -> int:
        if not self._fallback_active:
            try:
                return int(self._client.delete(*keys)) if keys else 0
            except (redis.exceptions.RedisError, OSError):
                self._fallback_active = True
        removed = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                removed += 1
        return removed

    def scan(
        self, cursor: int = 0, match: str | None = None, count: int = 100
    ) -> tuple[int, list[str]]:
        if not self._fallback_active:
            try:
                return self._client.scan(cursor=cursor, match=match, count=count)
            except (redis.exceptions.RedisError, OSError):
                self._fallback_active = True
        self._cleanup_expired()
        keys = list(self._store.keys())
        if match:
            keys = [k for k in keys if fnmatch.fnmatch(k, match)]
        return 0, keys

    def flushdb(self) -> bool:
        self._store.clear()
        if not self._fallback_active:
            try:
                self._client.flushdb()
            except (redis.exceptions.RedisError, OSError):
                self._fallback_active = True
        return True

    def ping(self) -> bool:
        if not self._fallback_active:
            try:
                return bool(self._client.ping())
            except (redis.exceptions.RedisError, OSError):
                self._fallback_active = True
        return True


_settings = Settings()
redis_client = ResilientRedis(_settings.redis_url)
