"""Pydantic schemas for multi-tenant job management and credit accounting."""

from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field

from demerivo.db.models import JobStatus, OperationType


class JobCreate(BaseModel):
    """Payload to initiate a new photo enhancement job."""
    operation_type: OperationType = Field(
        ...,
        description="Type of AI processing operation to apply"
    )
    input_key: str = Field(
        ...,
        description="Storage object key of the verified input image"
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional operation-specific tuning parameters (e.g. background_color, mask_key)"
    )
    idempotency_key: Optional[str] = Field(
        None,
        max_length=128,
        description="Client-supplied uniqueness key to prevent duplicate charging on retry"
    )


class JobResponse(BaseModel):
    """Standardized API response representing job state."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    user_id: str
    status: JobStatus
    operation_type: OperationType
    input_key: str
    output_key: Optional[str] = None
    result_url: Optional[str] = None
    progress: int = 0
    parameters: Dict[str, Any] = Field(default_factory=dict)
    credit_cost: int = 0
    idempotency_key: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class JobCancelResponse(BaseModel):
    """Response returned upon job cancellation request."""
    id: str
    status: JobStatus
    credits_refunded: int = 0
    message: str


class JobDownloadResponse(BaseModel):
    """Download authorization response."""
    download_url: str
    expires_in: int
    job_id: str
