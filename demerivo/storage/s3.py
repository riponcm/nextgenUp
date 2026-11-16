"""S3 and Cloudflare R2 compatible storage provider with native AWS SigV4."""

import datetime
import hashlib
import hmac
import urllib.parse
from typing import Optional

import httpx

from demerivo.storage.base import (
    StorageProvider,
    StorageError,
    FileNotFoundStorageError,
    InvalidPathStorageError,
)


class S3CompatibleStorage(StorageProvider):
    """Storage provider for S3-compatible object stores (Cloudflare R2, MinIO, AWS S3).
    
    Implements AWS Signature Version 4 (SigV4) for presigned URL generation
    and REST API operations. Private buckets by default.
    """

    def __init__(
        self,
        endpoint_url: str,
        bucket_name: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "auto",
        presigned_expiration_seconds: int = 3600,
    ):
        self.endpoint_url = endpoint_url.rstrip("/") if endpoint_url else "https://r2.cloudflarestorage.com"
        self.bucket_name = bucket_name
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region or "auto"
        self.default_expiration = presigned_expiration_seconds

        parsed = urllib.parse.urlparse(self.endpoint_url)
        self.host = parsed.netloc

    def _sign(self, key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def _get_signature_key(self, key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
        k_date = self._sign(("AWS4" + key).encode("utf-8"), date_stamp)
        k_region = self._sign(k_date, region_name)
        k_service = self._sign(k_region, service_name)
        k_signing = self._sign(k_service, "aws4_request")
        return k_signing

    def _sanitize_key(self, key: str) -> str:
        clean = key.lstrip("/\\").replace("\\", "/")
        if ".." in clean.split("/"):
            raise InvalidPathStorageError(f"Path traversal detected in S3 object key: '{key}'")
        return clean

    def _build_presigned_url(
        self,
        http_method: str,
        key: str,
        expires_in: int,
        content_type: Optional[str] = None
    ) -> str:
        """Generate an AWS SigV4 query-parameter authenticated presigned URL."""
        clean_key = self._sanitize_key(key)
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        canonical_uri = f"/{self.bucket_name}/{urllib.parse.quote(clean_key, safe='/-_.~')}"
        canonical_headers = f"host:{self.host}\n"
        signed_headers = "host"

        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        
        query_params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key_id}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": signed_headers,
        }

        canonical_query_string = urllib.parse.urlencode(sorted(query_params.items()))
        payload_hash = "UNSIGNED-PAYLOAD"

        canonical_request = (
            f"{http_method}\n"
            f"{canonical_uri}\n"
            f"{canonical_query_string}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        string_to_sign = (
            f"{algorithm}\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = self._get_signature_key(self.secret_access_key, date_stamp, self.region, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        query_params["X-Amz-Signature"] = signature
        final_query = urllib.parse.urlencode(query_params)
        return f"{self.endpoint_url}{canonical_uri}?{final_query}"

    def get_presigned_upload_url(
        self,
        key: str,
        content_type: str = "application/octet-stream",
        expires_in: Optional[int] = None
    ) -> str:
        exp = expires_in if expires_in is not None else self.default_expiration
        return self._build_presigned_url("PUT", key, exp, content_type)

    def get_presigned_download_url(
        self,
        key: str,
        expires_in: Optional[int] = None
    ) -> str:
        exp = expires_in if expires_in is not None else self.default_expiration
        return self._build_presigned_url("GET", key, exp)

    def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream"
    ) -> str:
        """Upload file via signed PUT request."""
        clean_key = self._sanitize_key(key)
        url = self.get_presigned_upload_url(clean_key, content_type=content_type, expires_in=900)
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.put(url, content=data, headers={"Content-Type": content_type})
                if resp.status_code not in (200, 201, 204):
                    raise StorageError(f"S3 upload failed with status {resp.status_code}: {resp.text[:200]}")
        except httpx.RequestError as e:
            raise StorageError(f"Network error connecting to S3 storage: {e}") from e
        return clean_key

    def download_file(self, key: str) -> bytes:
        """Download file via signed GET request."""
        clean_key = self._sanitize_key(key)
        url = self.get_presigned_download_url(clean_key, expires_in=900)
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url)
                if resp.status_code == 404:
                    raise FileNotFoundStorageError(f"S3 Object '{clean_key}' not found.")
                if resp.status_code != 200:
                    raise StorageError(f"S3 download failed with status {resp.status_code}: {resp.text[:200]}")
                return resp.content
        except httpx.RequestError as e:
            raise StorageError(f"Network error connecting to S3 storage: {e}") from e

    def delete_file(self, key: str) -> bool:
        """Delete file via signed DELETE request."""
        clean_key = self._sanitize_key(key)
        url = self._build_presigned_url("DELETE", clean_key, expires_in=300)
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.delete(url)
                return resp.status_code in (200, 204, 404)
        except httpx.RequestError as e:
            raise StorageError(f"Network error connecting to S3 storage: {e}") from e

    def exists(self, key: str) -> bool:
        """Check file existence via signed HEAD request."""
        clean_key = self._sanitize_key(key)
        url = self._build_presigned_url("HEAD", clean_key, expires_in=300)
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.head(url)
                return resp.status_code == 200
        except httpx.RequestError:
            return False

    def get_file_size(self, key: str) -> int:
        """Get object size via signed HEAD request."""
        clean_key = self._sanitize_key(key)
        url = self._build_presigned_url("HEAD", clean_key, expires_in=300)
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.head(url)
                if resp.status_code == 404:
                    raise FileNotFoundStorageError(f"S3 Object '{clean_key}' not found.")
                content_len = resp.headers.get("content-length")
                if content_len is not None:
                    return int(content_len)
                raise StorageError("Content-Length header not returned by S3.")
        except httpx.RequestError as e:
            raise StorageError(f"Network error connecting to S3 storage: {e}") from e
