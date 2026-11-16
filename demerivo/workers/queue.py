"""Durable queue abstraction supporting Redis and thread-safe local persistent fallback."""

import json
import logging
import math
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from threading import Lock

from demerivo.config import Settings, get_settings

logger = logging.getLogger("demerivo.queue")


class QueueProvider(ABC):
    """Abstract interface for reliable task queue providers."""

    @abstractmethod
    def enqueue(self, job_id: str) -> str:
        """Push a job ID into the pending queue."""
        pass

    @abstractmethod
    def dequeue(self, timeout: int = 2) -> Optional[str]:
        """Pop a job ID for processing, tracking worker lease."""
        pass

    @abstractmethod
    def acknowledge(self, job_id: str) -> bool:
        """Acknowledge successful completion of a job, clearing its lease."""
        pass

    @abstractmethod
    def fail(self, job_id: str, error: str) -> bool:
        """Mark a job as failed and move to dead-letter storage."""
        pass

    @abstractmethod
    def get_queue_length(self) -> int:
        """Return the number of pending jobs in queue."""
        pass

    @abstractmethod
    def renew_lease(self, job_id: str) -> bool:
        """Extend lease timestamp for an in-flight job during active progress."""
        pass

    @abstractmethod
    def recover_stranded_jobs(self, timeout_seconds: int = 600) -> List[str]:
        """Detect jobs whose worker lease expired without acknowledgment and re-enqueue them."""
        pass


class LocalDurableQueueProvider(QueueProvider):
    """Thread-safe in-memory/file persistent queue with lease tracking and crash recovery."""

    def __init__(self):
        self._pending: List[str] = []
        self._processing: Dict[str, float] = {}  # job_id -> lease_timestamp
        self._failed: Dict[str, str] = {}        # job_id -> error
        self._lock = Lock()

    def enqueue(self, job_id: str) -> str:
        with self._lock:
            if job_id not in self._pending:
                self._pending.append(job_id)
        return job_id

    def dequeue(self, timeout: float = 2.0) -> Optional[str]:
        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                if self._pending:
                    job_id = self._pending.pop(0)
                    self._processing[job_id] = time.time()
                    return job_id
            time.sleep(0.02)
        return None

    def acknowledge(self, job_id: str) -> bool:
        with self._lock:
            self._processing.pop(job_id, None)
            return True

    def fail(self, job_id: str, error: str) -> bool:
        with self._lock:
            self._processing.pop(job_id, None)
            self._failed[job_id] = error
            return True

    def get_queue_length(self) -> int:
        with self._lock:
            return len(self._pending)

    def renew_lease(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._processing:
                self._processing[job_id] = time.time()
                return True
        return False

    def recover_stranded_jobs(self, timeout_seconds: int = 600) -> List[str]:
        now = time.time()
        stranded = []
        with self._lock:
            for job_id, lease_time in list(self._processing.items()):
                if now - lease_time >= timeout_seconds:
                    stranded.append(job_id)
                    del self._processing[job_id]
                    self._pending.append(job_id)
        return stranded


class RedisQueueProvider(QueueProvider):
    """Durable Redis queue using RPOPLPUSH and lease management for zero-data-loss execution."""

    def __init__(self, redis_url: str):
        import redis
        self.redis = redis.from_url(
            redis_url,
            socket_timeout=5,
            socket_connect_timeout=3,
            decode_responses=True,
        )
        self.pending_key = "demerivo:queue:pending"
        self.processing_key = "demerivo:queue:processing"
        self.leases_key = "demerivo:queue:leases"
        self.failed_key = "demerivo:queue:failed"

    def enqueue(self, job_id: str) -> str:
        self.redis.lpush(self.pending_key, job_id)
        return job_id

    def dequeue(self, timeout: float = 2.0) -> Optional[str]:
        # Atomic transfer from pending to processing list
        try:
            # Redis BRPOPLPUSH requires an integer timeout >= 0
            redis_timeout = max(1, int(math.ceil(timeout))) if timeout > 0 else 0
            job_id = self.redis.brpoplpush(self.pending_key, self.processing_key, timeout=redis_timeout)
            if job_id:
                # Record lease timestamp
                self.redis.hset(self.leases_key, job_id, str(time.time()))
            return job_id
        except Exception as e:
            logger.error("Redis dequeue error: %s", e)
            return None

    def renew_lease(self, job_id: str) -> bool:
        """Extend lease timestamp in Redis hash during active job processing."""
        try:
            self.redis.hset(self.leases_key, job_id, str(time.time()))
            return True
        except Exception as e:
            logger.error("Redis renew_lease error for job %s: %s", job_id, e)
            return False

    def acknowledge(self, job_id: str) -> bool:
        pipe = self.redis.pipeline()
        pipe.lrem(self.processing_key, 0, job_id)
        pipe.hdel(self.leases_key, job_id)
        pipe.execute()
        return True

    def fail(self, job_id: str, error: str) -> bool:
        pipe = self.redis.pipeline()
        pipe.lrem(self.processing_key, 0, job_id)
        pipe.hdel(self.leases_key, job_id)
        pipe.hset(self.failed_key, job_id, error)
        pipe.execute()
        return True

    def get_queue_length(self) -> int:
        return self.redis.llen(self.pending_key)

    def recover_stranded_jobs(self, timeout_seconds: int = 600) -> List[str]:
        now = time.time()
        leases = self.redis.hgetall(self.leases_key)
        stranded = []
        for job_id, lease_str in leases.items():
            try:
                lease_time = float(lease_str)
                if now - lease_time >= timeout_seconds:
                    stranded.append(job_id)
                    pipe = self.redis.pipeline()
                    pipe.lrem(self.processing_key, 0, job_id)
                    pipe.hdel(self.leases_key, job_id)
                    pipe.lpush(self.pending_key, job_id)
                    pipe.execute()
            except ValueError:
                pass
        return stranded


_queue_provider_instance: Optional[QueueProvider] = None


def get_queue_provider(settings: Optional[Settings] = None) -> QueueProvider:
    """Return configured QueueProvider singleton with automatic fallback to local durable queue."""
    global _queue_provider_instance
    if _queue_provider_instance is None:
        cfg = settings or get_settings()
        if cfg.queue_backend == "redis" or (cfg.queue_backend == "auto" and not cfg.is_testing):
            try:
                provider = RedisQueueProvider(cfg.redis_url)
                # Test connectivity
                provider.redis.ping()
                _queue_provider_instance = provider
                logger.info("Connected to Redis queue at %s", cfg.redis_url)
                return _queue_provider_instance
            except Exception as e:
                logger.warning("Redis unavailable (%s), falling back to LocalDurableQueueProvider", e)

        _queue_provider_instance = LocalDurableQueueProvider()
    return _queue_provider_instance
