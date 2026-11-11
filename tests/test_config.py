"""Automated tests for configuration management."""

import os
import unittest
from demerivo.config import Settings, get_settings


class TestConfiguration(unittest.TestCase):
    def test_default_configuration(self):
        settings = Settings()
        self.assertEqual(settings.env, "development")
        self.assertEqual(settings.storage_provider, "local")
        self.assertFalse(settings.is_production)
        self.assertFalse(settings.is_testing)
        self.assertGreater(settings.max_upload_size_bytes, 0)
        self.assertIn(".jpg", settings.allowed_image_extensions)
        self.assertIn("image/png", settings.allowed_mime_types)

    def test_environment_override(self):
        os.environ["DEMERIVO_ENV"] = "production"
        os.environ["DEMERIVO_STORAGE_PROVIDER"] = "s3"
        os.environ["DEMERIVO_MAX_CONCURRENT_WORKERS"] = "4"
        try:
            settings = Settings()
            self.assertEqual(settings.env, "production")
            self.assertTrue(settings.is_production)
            self.assertEqual(settings.storage_provider, "s3")
            self.assertEqual(settings.max_concurrent_workers, 4)
        finally:
            os.environ.pop("DEMERIVO_ENV", None)
            os.environ.pop("DEMERIVO_STORAGE_PROVIDER", None)
            os.environ.pop("DEMERIVO_MAX_CONCURRENT_WORKERS", None)


if __name__ == "__main__":
    unittest.main()
