"""Automated tests for configuration management."""

import os
import unittest
from demerivo.config import Settings, get_settings


class TestConfiguration(unittest.TestCase):
    def test_default_configuration(self):
        prev_env = os.environ.pop("DEMERIVO_ENV", None)
        try:
            settings = Settings()
            self.assertEqual(settings.env, "development")
            self.assertEqual(settings.storage_provider, "local")
            self.assertFalse(settings.is_production)
            self.assertFalse(settings.is_testing)
            self.assertGreater(settings.max_upload_size_bytes, 0)
            self.assertIn(".jpg", settings.allowed_image_extensions)
            self.assertIn("image/png", settings.allowed_mime_types)
        finally:
            if prev_env is not None:
                os.environ["DEMERIVO_ENV"] = prev_env

    def test_environment_override(self):
        env_vars = [
            "DEMERIVO_ENV",
            "DEMERIVO_STORAGE_PROVIDER",
            "DEMERIVO_MAX_CONCURRENT_WORKERS",
        ]
        original_values = {var: os.environ.get(var) for var in env_vars}
        try:
            os.environ["DEMERIVO_ENV"] = "production"
            os.environ["DEMERIVO_STORAGE_PROVIDER"] = "s3"
            os.environ["DEMERIVO_MAX_CONCURRENT_WORKERS"] = "4"

            settings = Settings()
            self.assertEqual(settings.env, "production")
            self.assertTrue(settings.is_production)
            self.assertEqual(settings.storage_provider, "s3")
            self.assertEqual(settings.max_concurrent_workers, 4)
        finally:
            for var, orig in original_values.items():
                if orig is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = orig


if __name__ == "__main__":
    unittest.main()
