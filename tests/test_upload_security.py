"""Automated tests for image upload security validation."""

import io
import unittest
from PIL import Image

from demerivo.config import Settings
from demerivo.security.upload_validator import (
    UploadValidationError,
    sanitize_filename,
    validate_image_upload,
)


def create_test_image(format="PNG", size=(100, 100), color=(255, 0, 0)) -> bytes:
    """Helper to synthesize valid image binary in memory."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=format)
    return buf.getvalue()


class TestUploadSecurity(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            max_upload_size_bytes=1024 * 1024,  # 1 MB limit for test
            max_image_width=2000,
            max_image_height=2000,
            max_image_pixels=4_000_000,
        )

    def test_valid_png_upload(self):
        png_bytes = create_test_image(format="PNG", size=(300, 200))
        res = validate_image_upload(png_bytes, "my_photo.png", settings=self.settings)

        self.assertTrue(res.is_valid)
        self.assertEqual(res.format, "PNG")
        self.assertEqual(res.mime_type, "image/png")
        self.assertEqual(res.extension, ".png")
        self.assertEqual(res.width, 300)
        self.assertEqual(res.height, 200)
        self.assertEqual(res.sanitized_filename, "my_photo.png")
        self.assertTrue(res.secure_object_key.startswith("uploads/"))
        self.assertTrue(res.secure_object_key.endswith(".png"))

    def test_valid_jpeg_upload(self):
        jpeg_bytes = create_test_image(format="JPEG", size=(150, 150))
        res = validate_image_upload(jpeg_bytes, "vacation.jpg", settings=self.settings)

        self.assertTrue(res.is_valid)
        self.assertEqual(res.format, "JPEG")
        self.assertEqual(res.mime_type, "image/jpeg")
        self.assertEqual(res.extension, ".jpg")

    def test_valid_webp_upload(self):
        webp_bytes = create_test_image(format="WEBP", size=(80, 80))
        res = validate_image_upload(webp_bytes, "banner.webp", settings=self.settings)

        self.assertTrue(res.is_valid)
        self.assertEqual(res.format, "WEBP")
        self.assertEqual(res.mime_type, "image/webp")

    def test_empty_file_rejected(self):
        with self.assertRaises(UploadValidationError) as ctx:
            validate_image_upload(b"", "empty.png", settings=self.settings)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_oversized_file_rejected(self):
        huge_bytes = b"X" * (self.settings.max_upload_size_bytes + 1024)
        with self.assertRaises(UploadValidationError) as ctx:
            validate_image_upload(huge_bytes, "huge.png", settings=self.settings)
        self.assertIn("exceeds maximum allowed", str(ctx.exception))

    def test_unsupported_magic_signature_rejected(self):
        fake_binary = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 50  # Windows executable
        with self.assertRaises(UploadValidationError) as ctx:
            validate_image_upload(fake_binary, "program.exe", settings=self.settings)
        self.assertIn("unrecognized image binary signature", str(ctx.exception))

    def test_spoofed_extension_rejected(self):
        text_script = b"#!/bin/bash\necho 'hello world'\n"
        with self.assertRaises(UploadValidationError) as ctx:
            validate_image_upload(text_script, "script.png", settings=self.settings)
        self.assertIn("unrecognized image binary signature", str(ctx.exception))

    def test_corrupted_image_header_rejected(self):
        # Starts with PNG magic signature but payload is random garbage
        corrupted = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        with self.assertRaises(UploadValidationError) as ctx:
            validate_image_upload(corrupted, "corrupt.png", settings=self.settings)
        self.assertIn("corrupted or invalid", str(ctx.exception).lower())

    def test_dimension_limits_enforced(self):
        oversized_img = create_test_image(format="PNG", size=(2500, 500))  # Width > 2000
        with self.assertRaises(UploadValidationError) as ctx:
            validate_image_upload(oversized_img, "wide.png", settings=self.settings)
        self.assertIn("exceed maximum allowed limits", str(ctx.exception))

    def test_filename_sanitization_and_traversal(self):
        # POSIX directory traversal
        self.assertEqual(sanitize_filename("../../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("../../var/log/syslog"), "syslog")
        # Windows-style directory traversal (both lower and upper case)
        self.assertEqual(sanitize_filename("..\\..\\windows\\system32\\cmd.exe"), "cmd.exe")
        self.assertEqual(sanitize_filename("..\\..\\Windows\\System32\\cmd.exe"), "cmd.exe")
        self.assertEqual(sanitize_filename(r"..\..\Windows\System32\cmd.exe"), "cmd.exe")
        # Mixed separators and path traversal
        self.assertEqual(sanitize_filename(r"folder\subfolder/traversal\photo.jpg"), "photo.jpg")
        # Hidden files and extensions
        self.assertEqual(sanitize_filename(".hidden_file.jpg"), "hidden_file.jpg")
        self.assertEqual(sanitize_filename("valid_name-123.PNG"), "valid_name-123.PNG")
        # Empty, None, or directory-only traversal
        self.assertEqual(sanitize_filename(""), "image.png")
        self.assertEqual(sanitize_filename(None), "image.png")
        self.assertEqual(sanitize_filename("../../.."), "image.png")
        self.assertEqual(sanitize_filename("..\\..\\.."), "image.png")


if __name__ == "__main__":
    unittest.main()
