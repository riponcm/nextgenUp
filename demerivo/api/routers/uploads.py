"""Upload endpoints: Presigned URL generator and secure direct file receiver."""

from pathlib import Path
from typing import Optional, Tuple
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from demerivo.auth.dependencies import get_current_tenant, get_current_user
from demerivo.config import Settings, get_settings
from demerivo.db.models import TenantModel, TenantRole, UserModel
from demerivo.security.rate_limiter import rate_limit_uploads
from demerivo.security.upload_validator import (
    UploadValidationError,
    generate_secure_object_key,
    sanitize_filename,
    validate_image_upload,
)
from demerivo.storage.base import StorageProvider
from demerivo.storage.factory import get_storage

router = APIRouter(prefix="/api/v1/uploads", tags=["Uploads & Media Ingestion"])


class PresignedUploadRequest(BaseModel):
    """Client request to generate a direct-to-storage presigned upload URL."""
    filename: str = Field(..., json_schema_extra={"example": "portrait.jpg"}, description="Original filename")
    content_type: str = Field(..., json_schema_extra={"example": "image/jpeg"}, description="MIME type of the image")
    file_size_bytes: int = Field(..., gt=0, description="Size in bytes of the payload")


class PresignedUploadResponse(BaseModel):
    """Direct-to-storage presigned upload parameters."""
    upload_url: str
    object_key: str
    method: str = "PUT"
    expires_in: int


class DirectUploadResponse(BaseModel):
    """Result returned after direct multipart image upload and verification."""
    object_key: str
    filename: str
    format: str
    width: int
    height: int
    size_bytes: int


@router.post(
    "/presigned",
    response_model=PresignedUploadResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_uploads)],
)
def create_presigned_upload(
    req: PresignedUploadRequest,
    current_user: UserModel = Depends(get_current_user),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    storage: StorageProvider = Depends(get_storage),
):
    """Generate a presigned upload URL for direct browser-to-R2 upload with tenant namespace."""
    tenant, _ = tenant_context

    ext = Path(req.filename).suffix.lower()
    if ext not in settings.allowed_image_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{ext}'. Allowed: {sorted(settings.allowed_image_extensions)}",
        )

    if req.content_type not in settings.allowed_mime_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported MIME type '{req.content_type}'. Allowed: {sorted(settings.allowed_mime_types)}",
        )

    if req.file_size_bytes > settings.max_upload_size_bytes:
        max_mb = settings.max_upload_size_bytes / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds maximum allowed limit of {max_mb:.1f} MB.",
        )

    secure_key = generate_secure_object_key(prefix=f"uploads/{tenant.id}", extension=ext)
    upload_url = storage.get_presigned_upload_url(
        key=secure_key,
        content_type=req.content_type,
        expires_in=settings.s3_presigned_expiration_seconds,
    )

    return PresignedUploadResponse(
        upload_url=upload_url,
        object_key=secure_key,
        method="PUT",
        expires_in=settings.s3_presigned_expiration_seconds,
    )


@router.post(
    "/file",
    response_model=DirectUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_uploads)],
)
def upload_image_direct(
    file: UploadFile = File(..., description="Image file to upload and validate"),
    current_user: UserModel = Depends(get_current_user),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    settings: Settings = Depends(get_settings),
    storage: StorageProvider = Depends(get_storage),
):
    """Direct multipart image upload with full magic-bytes verification and tenant scoping."""
    tenant, _ = tenant_context
    try:
        content = file.file.read()
        res = validate_image_upload(
            data=content,
            original_filename=file.filename or "upload.png",
            settings=settings,
        )

        safe_filename = sanitize_filename(file.filename or "image.png")
        secure_key = generate_secure_object_key(
            prefix=f"uploads/{tenant.id}",
            extension=f".{res.format.lower()}",
        )

        storage.upload_file(
            key=secure_key,
            data=content,
            content_type=res.mime_type,
        )

        return DirectUploadResponse(
            object_key=secure_key,
            filename=safe_filename,
            format=res.format,
            width=res.width,
            height=res.height,
            size_bytes=res.size_bytes,
        )
    except UploadValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
