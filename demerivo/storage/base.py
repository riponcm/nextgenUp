"""Abstract storage provider interface."""

from abc import ABC, abstractmethod
from typing import Optional


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class FileNotFoundStorageError(StorageError):
    """Raised when an object key does not exist."""
    pass


class InvalidPathStorageError(StorageError):
    """Raised on invalid key or path traversal attempt."""
    pass


class StorageProvider(ABC):
    """Abstract interface for all media storage backends."""

    @abstractmethod
    def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream"
    ) -> str:
        """Upload raw binary data under the specified object key.
        
        Returns the object key or storage reference.
        """
        pass

    @abstractmethod
    def download_file(self, key: str) -> bytes:
        """Download binary data for the specified key.
        
        Raises FileNotFoundStorageError if the key does not exist.
        """
        pass

    @abstractmethod
    def delete_file(self, key: str) -> bool:
        """Delete an object key. Returns True if deleted or already absent."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if the object key exists."""
        pass

    @abstractmethod
    def get_file_size(self, key: str) -> int:
        """Return the size in bytes of the object key."""
        pass

    @abstractmethod
    def get_presigned_upload_url(
        self,
        key: str,
        content_type: str = "application/octet-stream",
        expires_in: int = 3600
    ) -> str:
        """Generate a presigned URL allowing client HTTP PUT upload directly to storage."""
        pass

    @abstractmethod
    def get_presigned_download_url(
        self,
        key: str,
        expires_in: int = 3600
    ) -> str:
        """Generate a presigned URL allowing client HTTP GET download directly from storage."""
        pass
