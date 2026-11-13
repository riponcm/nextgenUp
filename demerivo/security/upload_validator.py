"""Image upload validation, magic bytes inspection, and security sanitization."""

import io
import posixpath
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set, Tuple

from PIL import Image

from demerivo.config import Settings, get_settings


class UploadValidationError(Exception):
    """Raised when an uploaded file fails security or format validation."""
    pass


@dataclass(frozen=True)
class ImageValidationResult:
    """Detailed metadata from a verified image upload."""
    is_valid: bool
    format: str            # E.g. 'JPEG', 'PNG', 'WEBP'
    mime_type: str         # Verified MIME, e.g. 'image/jpeg'
    extension: str        # E.g. '.jpg', '.png'
    width: int
    height: int
    size_bytes: int
    sanitized_filename: str
    secure_object_key: str


MAGIC_SIGNATURES: Tuple[Tuple[bytes, str, str, str], ...] = (
    (b"\xFF\xD8\xFF", "JPEG", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "PNG", "image/png", ".png"),
    (b"BM", "BMP", "image/bmp", ".bmp"),
    (b"II*\x00", "TIFF", "image/tiff", ".tiff"),
    (b"MM\x00*", "TIFF", "image/tiff", ".tiff"),
)

ILLEGAL_FILENAME_CHARS = re.compile(r'[^a-zA-Z0-9_.\-]')


def sanitize_filename(filename: Optional[str]) -> str:
    """Sanitize user-provided filename, stripping directory separators and unsafe characters."""
    if not filename:
        return "image.png"
    # Normalize directory separators across Windows and POSIX environments
    normalized = str(filename).replace("\\", "/")
    # Strip any directory path components using POSIX basename
    basename = posixpath.basename(normalized.rstrip("/"))
    # Strip leading dots (hidden files)
    basename = basename.lstrip(".")
    # Replace unsafe characters
    sanitized = ILLEGAL_FILENAME_CHARS.sub("_", basename)
    return sanitized[:128] or "image.png"


def detect_magic_format(data: bytes) -> Optional[Tuple[str, str, str]]:
    """Inspect the first bytes of data to reliably detect the image format without trusting headers.
    
    Returns (format_name, mime_type, extension) or None.
    """
    if len(data) < 16:
        return None

    # Check WebP signature (RIFF....WEBP)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ("WEBP", "image/webp", ".webp")

    for magic, fmt, mime, ext in MAGIC_SIGNATURES:
        if data.startswith(magic):
            return (fmt, mime, ext)

    return None


def generate_secure_object_key(prefix: str = "uploads", extension: str = ".png") -> str:
    """Generate an unguessable, collision-free server object key."""
    ext = extension if extension.startswith(".") else f".{extension}"
    random_id = uuid.uuid4().hex
    return f"{prefix}/{random_id}{ext.lower()}"


def validate_image_upload(
    data: bytes,
    original_filename: Optional[str] = None,
    settings: Optional[Settings] = None
) -> ImageValidationResult:
    """Perform rigorous multi-stage security validation on an uploaded image payload.
    
    1. Size boundary check.
    2. Magic bytes verification (independent of user HTTP headers).
    3. Pillow image header decode & verification (detecting corrupted or malformed payloads).
    4. Dimension and megapixel boundary checks (preventing decompression bombs).
    5. Generation of sanitized filename and random server object identifier.
    """
    cfg = settings or get_settings()

    # 1. File size check
    size = len(data)
    if size == 0:
        raise UploadValidationError("Uploaded file is empty (0 bytes).")
    if size > cfg.max_upload_size_bytes:
        max_mb = cfg.max_upload_size_bytes / (1024 * 1024)
        raise UploadValidationError(f"File size ({size / (1024 * 1024):.1f} MB) exceeds maximum allowed {max_mb:.1f} MB.")

    # 2. Magic signature check
    magic_info = detect_magic_format(data)
    if not magic_info:
        raise UploadValidationError("Unsupported or unrecognized image binary signature.")

    fmt_magic, mime_magic, ext_magic = magic_info
    if mime_magic not in cfg.allowed_mime_types:
        raise UploadValidationError(f"MIME type '{mime_magic}' is not permitted.")

    # 3. Pillow header verification & dimension checks
    try:
        # Enforce decompression bomb protection
        Image.MAX_IMAGE_PIXELS = cfg.max_image_pixels

        stream = io.BytesIO(data)
        with Image.open(stream) as img:
            # Verify structural integrity of image headers
            img.verify()
            fmt = img.format
            width, height = img.size

            # Ensure format matches detected magic bytes
            if fmt and fmt.upper() != fmt_magic and not (fmt.upper() == "MPO" and fmt_magic == "JPEG"):
                raise UploadValidationError(f"Header format '{fmt}' does not match payload signature '{fmt_magic}'.")

    except Image.DecompressionBombError as e:
        raise UploadValidationError(f"Image exceeds safe decompression pixel limit ({cfg.max_image_pixels} px): {e}") from e
    except Exception as e:
        raise UploadValidationError(f"Corrupted or invalid image data: {e}") from e

    # 4. Dimension limits
    if width <= 0 or height <= 0:
        raise UploadValidationError("Invalid image dimensions (zero or negative).")

    if width > cfg.max_image_width or height > cfg.max_image_height:
        raise UploadValidationError(
            f"Image dimensions ({width}x{height}) exceed maximum allowed limits "
            f"({cfg.max_image_width}x{cfg.max_image_height})."
        )

    if (width * height) > cfg.max_image_pixels:
        raise UploadValidationError(
            f"Total image pixels ({width * height:,}) exceed maximum allowed limit ({cfg.max_image_pixels:,})."
        )

    # 5. Sanitization and key generation
    safe_name = sanitize_filename(original_filename)
    secure_key = generate_secure_object_key(prefix="uploads", extension=ext_magic)

    return ImageValidationResult(
        is_valid=True,
        format=fmt_magic,
        mime_type=mime_magic,
        extension=ext_magic,
        width=width,
        height=height,
        size_bytes=size,
        sanitized_filename=safe_name,
        secure_object_key=secure_key,
    )
