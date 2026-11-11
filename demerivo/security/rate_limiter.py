"""Distributed Redis and local in-memory sliding window rate limiter."""

import hashlib
import logging
import time
from collections import defaultdict
from threading import Lock
from typing import Callable, Dict, List, Optional

from fastapi import HTTPException, Request, status

from demerivo.config import Settings, get_settings

logger = logging.getLogger("demerivo.security.ratelimit")


class SlidingWindowRateLimiter:
    """Hybrid rate limiter with Redis distributed backend and local memory fallback."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._local_records: Dict[str, List[float]] = defaultdict(list)
        self._lock = Lock()
        self._redis_client = None
        self._init_redis()

    def _init_redis(self):
        """Attempt connection to Redis if configured."""
        if self.settings.redis_url and self.settings.queue_backend in ("redis", "auto"):
            try:
                import redis
                client = redis.from_url(
                    self.settings.redis_url,
                    socket_timeout=2,
                    socket_connect_timeout=2,
                    decode_responses=True,
                )
                client.ping()
                self._redis_client = client
                logger.info("Rate limiter connected to Redis backend.")
            except Exception as e:
                logger.warning("Redis rate limiter unavailable (%s). Using thread-safe in-memory fallback.", e)
                self._redis_client = None

    def is_allowed(self, key: str, max_requests: int, window_seconds: float = 60.0) -> bool:
        """Check if request is permitted under sliding window limit."""
        now = time.time()
        cutoff = now - window_seconds

        # 1. Try Redis distributed rate limiting
        if self._redis_client is not None:
            try:
                pipe = self._redis_client.pipeline()
                redis_key = f"ratelimit:{key}"
                pipe.zremrangebyscore(redis_key, 0, cutoff)
                pipe.zcard(redis_key)
                pipe.zadd(redis_key, {str(now): now})
                pipe.expire(redis_key, int(window_seconds) + 5)
                results = pipe.execute()

                current_count = results[1]
                if current_count >= max_requests:
                    return False
                return True
            except Exception as e:
                logger.warning("Redis rate check failed (%s). Falling back to memory limiter.", e)
                self._redis_client = None

        # 2. Local in-memory sliding window fallback
        with self._lock:
            timestamps = [t for t in self._local_records[key] if t > cutoff]
            if len(timestamps) >= max_requests:
                self._local_records[key] = timestamps
                return False

            timestamps.append(now)
            self._local_records[key] = timestamps
            return True

    def reset(self):
        """Reset all rate limiter state (useful for tests)."""
        with self._lock:
            self._local_records.clear()
        if self._redis_client is not None:
            try:
                # Flush ratelimit keys
                keys = self._redis_client.keys("ratelimit:*")
                if keys:
                    self._redis_client.delete(*keys)
            except Exception:
                pass


_global_limiter: Optional[SlidingWindowRateLimiter] = None


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Return singleton instance of SlidingWindowRateLimiter."""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = SlidingWindowRateLimiter()
    return _global_limiter


def create_rate_limiter_dependency(
    endpoint_tag: str,
    max_requests_getter: Optional[Callable[[Settings], int]] = None,
    default_max_requests: int = 60,
    window_seconds: float = 60.0,
):
    """Factory generating a FastAPI dependency enforcing route-specific rate limits."""

    def dependency(request: Request):
        settings = get_settings()
        limiter = get_rate_limiter()

        # Extract client IP safely
        client_ip = request.client.host if request.client else "127.0.0.1"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        # Extract authenticated identity token if present
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            # Hash bearer token to bind identity without rotating IP bypass
            ident = hashlib.sha256(auth_header.encode("utf-8")).hexdigest()[:16]
            key = f"{endpoint_tag}:auth:{ident}"
        else:
            key = f"{endpoint_tag}:ip:{client_ip}"

        max_reqs = max_requests_getter(settings) if max_requests_getter else default_max_requests

        if not limiter.is_allowed(key=key, max_requests=max_reqs, window_seconds=window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {endpoint_tag}. Please wait before retrying.",
                headers={"Retry-After": str(int(window_seconds))},
            )

    return dependency


# Standard Route Rate Limiters
rate_limit_auth = create_rate_limiter_dependency(
    endpoint_tag="auth",
    max_requests_getter=lambda s: s.rate_limit_auth_requests_per_minute,
    default_max_requests=10,
)

rate_limit_uploads = create_rate_limiter_dependency(
    endpoint_tag="uploads",
    max_requests_getter=lambda s: s.rate_limit_upload_requests_per_minute,
    default_max_requests=30,
)

rate_limit_jobs = create_rate_limiter_dependency(
    endpoint_tag="jobs",
    max_requests_getter=lambda s: s.rate_limit_jobs_requests_per_minute,
    default_max_requests=30,
)

rate_limit_general = create_rate_limiter_dependency(
    endpoint_tag="general",
    max_requests_getter=lambda s: s.rate_limit_requests_per_minute,
    default_max_requests=60,
)
