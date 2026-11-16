"""Storage provider factory supporting dependency injection."""

from functools import lru_cache
from typing import Optional

from demerivo.config import Settings, get_settings
from demerivo.storage.base import StorageProvider
from demerivo.storage.local import LocalStorage
from demerivo.storage.s3 import S3CompatibleStorage


_storage_instance: Optional[StorageProvider] = None


def get_storage(settings: Optional[Settings] = None) -> StorageProvider:
    """Return the configured StorageProvider singleton based on settings."""
    global _storage_instance
    if _storage_instance is not None and settings is None:
        return _storage_instance

    cfg = settings or get_settings()

    if cfg.storage_provider == "s3":
        inst = S3CompatibleStorage(
            endpoint_url=cfg.s3_endpoint_url,
            bucket_name=cfg.s3_bucket_name,
            access_key_id=cfg.s3_access_key_id,
            secret_access_key=cfg.s3_secret_access_key,
            region=cfg.s3_region,
            presigned_expiration_seconds=cfg.s3_presigned_expiration_seconds,
        )
    else:
        inst = LocalStorage(base_dir=cfg.local_storage_dir, secret_key=cfg.supabase_jwt_secret)

    if settings is None:
        _storage_instance = inst
    return inst
