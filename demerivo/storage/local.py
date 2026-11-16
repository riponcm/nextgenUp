"""Local disk storage provider for development and testing."""

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Optional

from demerivo.storage.base import (
    StorageProvider,
    StorageError,
    FileNotFoundStorageError,
    InvalidPathStorageError,
)


class LocalStorage(StorageProvider):
    """Storage provider saving files to a local directory with traversal protection."""

    def __init__(self, base_dir: str = "./storage_data", secret_key: str = "dev-secret-salt"):
        self.base_dir = Path(base_dir).resolve()
        self.secret_key = secret_key.encode("utf-8")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, key: str) -> Path:
        """Resolve an object key to an absolute Path and ensure no directory traversal."""
        if not key or key.startswith(("/", "\\")) or Path(key).is_absolute() or (len(key) > 1 and key[1] == ":"):
            raise InvalidPathStorageError(f"Access denied: absolute path rejected for key '{key}'")

        # Check for traversal tokens
        normalized = key.replace("\\", "/")
        parts = normalized.split("/")
        if ".." in parts or "." in parts:
            raise InvalidPathStorageError(f"Access denied: path traversal token detected in key '{key}'")

        target_path = (self.base_dir / normalized).resolve()
        try:
            target_path.relative_to(self.base_dir)
        except ValueError:
            raise InvalidPathStorageError(f"Access denied: path traversal detected for key '{key}'")
            
        return target_path

    def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream"
    ) -> str:
        target_path = self._resolve_key(key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_path.write_bytes(data)
        except OSError as e:
            raise StorageError(f"Failed to write local file '{key}': {e}") from e
        return key

    def download_file(self, key: str) -> bytes:
        target_path = self._resolve_key(key)
        if not target_path.is_file():
            raise FileNotFoundStorageError(f"Object '{key}' not found in local storage.")
        try:
            return target_path.read_bytes()
        except OSError as e:
            raise StorageError(f"Failed to read local file '{key}': {e}") from e

    def delete_file(self, key: str) -> bool:
        target_path = self._resolve_key(key)
        if target_path.is_file():
            try:
                target_path.unlink()
                return True
            except OSError as e:
                raise StorageError(f"Failed to delete local file '{key}': {e}") from e
        return False

    def exists(self, key: str) -> bool:
        target_path = self._resolve_key(key)
        return target_path.is_file()

    def get_file_size(self, key: str) -> int:
        target_path = self._resolve_key(key)
        if not target_path.is_file():
            raise FileNotFoundStorageError(f"Object '{key}' not found.")
        return target_path.stat().st_size

    def _generate_signature(self, key: str, expires_at: int) -> str:
        payload = f"{key}:{expires_at}".encode("utf-8")
        return hmac.new(self.secret_key, payload, hashlib.sha256).hexdigest()

    def get_presigned_upload_url(
        self,
        key: str,
        content_type: str = "application/octet-stream",
        expires_in: int = 3600
    ) -> str:
        expires_at = int(time.time()) + expires_in
        sig = self._generate_signature(key, expires_at)
        return f"/api/v1/uploads/direct/{key}?expires={expires_at}&sig={sig}"

    def get_presigned_download_url(
        self,
        key: str,
        expires_in: int = 3600
    ) -> str:
        expires_at = int(time.time()) + expires_in
        sig = self._generate_signature(key, expires_at)
        return f"/api/v1/storage/file/{key}?expires={expires_at}&sig={sig}"
