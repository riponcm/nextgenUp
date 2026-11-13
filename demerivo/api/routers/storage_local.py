"""Local development storage bridge for presigned URL endpoints."""

import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from demerivo.config import Settings, get_settings
from demerivo.storage.base import FileNotFoundStorageError, InvalidPathStorageError, StorageProvider
from demerivo.storage.factory import get_storage
from demerivo.storage.local import LocalStorage

router = APIRouter(prefix="/api/v1", tags=["Local Storage Simulation"])


@router.put("/uploads/direct/{key:path}", status_code=status.HTTP_200_OK)
async def local_presigned_upload(
    key: str,
    request: Request,
    expires: int = Query(...),
    sig: str = Query(...),
    settings: Settings = Depends(get_settings),
    storage: StorageProvider = Depends(get_storage),
):
    """Local simulation of an S3 presigned PUT upload."""
    if not isinstance(storage, LocalStorage):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Direct local PUT is only for LocalStorage.")

    # Verify expiration
    if int(time.time()) > expires:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Presigned upload URL has expired.")

    # Verify signature
    expected_sig = storage._generate_signature(key, expires)
    if sig != expected_sig:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid presigned upload signature.")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload body.")

    try:
        storage.upload_file(key, body)
    except InvalidPathStorageError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    return {"status": "uploaded", "key": key}


@router.get("/storage/file/{key:path}", status_code=status.HTTP_200_OK)
def local_presigned_download(
    key: str,
    expires: int = Query(...),
    sig: str = Query(...),
    settings: Settings = Depends(get_settings),
    storage: StorageProvider = Depends(get_storage),
):
    """Local simulation of an S3 presigned GET download."""
    if not isinstance(storage, LocalStorage):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Direct local GET is only for LocalStorage.")

    # Verify expiration
    if int(time.time()) > expires:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Presigned download URL has expired.")

    # Verify signature
    expected_sig = storage._generate_signature(key, expires)
    if sig != expected_sig:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid presigned download signature.")

    try:
        data = storage.download_file(key)
        return Response(content=data, media_type="application/octet-stream")
    except FileNotFoundStorageError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidPathStorageError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
