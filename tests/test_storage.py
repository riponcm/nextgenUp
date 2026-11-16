"""Automated tests for LocalStorage and S3CompatibleStorage."""

import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import httpx

from demerivo.storage.base import (
    FileNotFoundStorageError,
    InvalidPathStorageError,
    StorageError,
)
from demerivo.storage.local import LocalStorage
from demerivo.storage.s3 import S3CompatibleStorage


class TestLocalStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorage(base_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_upload_and_download(self):
        key = "uploads/test.png"
        payload = b"dummy-image-bytes-12345"
        saved_key = self.storage.upload_file(key, payload, content_type="image/png")
        self.assertEqual(saved_key, key)
        self.assertTrue(self.storage.exists(key))
        self.assertEqual(self.storage.get_file_size(key), len(payload))

        downloaded = self.storage.download_file(key)
        self.assertEqual(downloaded, payload)

    def test_delete_file(self):
        key = "uploads/to_delete.png"
        self.storage.upload_file(key, b"abc")
        self.assertTrue(self.storage.exists(key))
        deleted = self.storage.delete_file(key)
        self.assertTrue(deleted)
        self.assertFalse(self.storage.exists(key))
        # Deleting non-existent file returns False
        self.assertFalse(self.storage.delete_file(key))

    def test_file_not_found(self):
        with self.assertRaises(FileNotFoundStorageError):
            self.storage.download_file("non_existent_key.png")

    def test_path_traversal_prevention(self):
        malicious_keys = [
            "../../etc/passwd",
            "..\\..\\windows\\system32\\calc.exe",
            "/absolute/root/file.png",
            "uploads/../../escape.png",
        ]
        for key in malicious_keys:
            with self.assertRaises(InvalidPathStorageError):
                self.storage.upload_file(key, b"exploit")

    def test_presigned_urls(self):
        key = "uploads/sample.png"
        upload_url = self.storage.get_presigned_upload_url(key, expires_in=1800)
        self.assertIn("/api/v1/uploads/direct/uploads/sample.png", upload_url)
        self.assertIn("expires=", upload_url)
        self.assertIn("sig=", upload_url)

        download_url = self.storage.get_presigned_download_url(key, expires_in=1800)
        self.assertIn("/api/v1/storage/file/uploads/sample.png", download_url)
        self.assertIn("sig=", download_url)


class TestS3CompatibleStorage(unittest.TestCase):
    def setUp(self):
        self.storage = S3CompatibleStorage(
            endpoint_url="https://test-account.r2.cloudflarestorage.com",
            bucket_name="demerivo-test-bucket",
            access_key_id="TEST_ACCESS_KEY_ID_123",
            secret_access_key="TEST_SECRET_ACCESS_KEY_456",
            region="auto",
            presigned_expiration_seconds=3600,
        )

    def test_presigned_upload_url_generation(self):
        key = "uploads/avatar.jpg"
        url = self.storage.get_presigned_upload_url(key, content_type="image/jpeg", expires_in=1800)

        self.assertTrue(url.startswith("https://test-account.r2.cloudflarestorage.com/demerivo-test-bucket/uploads/avatar.jpg?"))
        self.assertIn("X-Amz-Algorithm=AWS4-HMAC-SHA256", url)
        self.assertIn("X-Amz-Credential=TEST_ACCESS_KEY_ID_123", url)
        self.assertIn("X-Amz-Expires=1800", url)
        self.assertIn("X-Amz-Signature=", url)

    def test_presigned_download_url_generation(self):
        key = "outputs/enhanced.png"
        url = self.storage.get_presigned_download_url(key, expires_in=3600)

        self.assertTrue(url.startswith("https://test-account.r2.cloudflarestorage.com/demerivo-test-bucket/outputs/enhanced.png?"))
        self.assertIn("X-Amz-Algorithm=AWS4-HMAC-SHA256", url)
        self.assertIn("X-Amz-Credential=", url)
        self.assertIn("X-Amz-Signature=", url)

    def test_path_traversal_prevention(self):
        with self.assertRaises(InvalidPathStorageError):
            self.storage.get_presigned_upload_url("../../secret.png")

    @patch("httpx.Client")
    def test_mocked_upload_and_download(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        # Mock successful PUT upload
        mock_put_resp = MagicMock()
        mock_put_resp.status_code = 200
        mock_client.put.return_value = mock_put_resp

        key = "uploads/photo.png"
        saved = self.storage.upload_file(key, b"binary_data", content_type="image/png")
        self.assertEqual(saved, key)
        self.assertTrue(mock_client.put.called)

        # Mock successful GET download
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.content = b"downloaded_bytes"
        mock_client.get.return_value = mock_get_resp

        downloaded = self.storage.download_file(key)
        self.assertEqual(downloaded, b"downloaded_bytes")
        self.assertTrue(mock_client.get.called)

    @patch("httpx.Client")
    def test_mocked_exists_and_delete(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_head_resp = MagicMock()
        mock_head_resp.status_code = 200
        mock_client.head.return_value = mock_head_resp
        self.assertTrue(self.storage.exists("file.png"))

        mock_head_resp.status_code = 404
        self.assertFalse(self.storage.exists("missing.png"))

        mock_del_resp = MagicMock()
        mock_del_resp.status_code = 204
        mock_client.delete.return_value = mock_del_resp
        self.assertTrue(self.storage.delete_file("file.png"))


if __name__ == "__main__":
    unittest.main()
