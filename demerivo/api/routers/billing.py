"""Subscription Billing and Payment API Endpoints.

Provides plan listings, subscription status, checkout generation, payment verification,
subscription cancellation, billing history, and signed webhook handling.
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import (
    get_current_tenant,
    get_current_user,
)
from demerivo.billing import (
    BillingService,
    InvalidSignatureError,
    PaymentProviderError,
    get_billing_service,
)
from demerivo.db.models import TenantModel, TenantRole, UserModel
from demerivo.db.session import get_db

router = APIRouter(prefix="/billing", tags=["Billing & Subscriptions"])


# Pydantic Schemas
class CreateCheckoutRequest(BaseModel):
    plan_code: str


class VerifyPaymentRequest(BaseModel):
    order_id: str
    payment_id: str
    signature: str
    plan_code: str


class CancelSubscriptionRequest(BaseModel):
    immediately: bool = False


@router.get("/plans")
def list_available_plans(
    billing_service: BillingService = Depends(get_billing_service),
) -> Dict[str, Any]:
    """Retrieve all available subscription plans with pricing and features."""
    plans = billing_service.get_plans()
    return {"plans": plans}


@router.get("/subscription")
def get_current_subscription(
    db: Session = Depends(get_db),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    billing_service: BillingService = Depends(get_billing_service),
) -> Dict[str, Any]:
    """Retrieve the current active subscription for the tenant."""
    tenant, _ = tenant_context
    sub = billing_service.get_or_create_tenant_subscription(db, tenant.id)
    plan_dict = billing_service.sync_plans_to_db(db)
    current_plan = plan_dict.get(sub.plan_code)
    
    return {
        "subscription": sub.to_dict(),
        "plan": current_plan.to_dict() if current_plan else None,
    }


@router.post("/subscription", status_code=status.HTTP_201_CREATED)
def create_subscription_checkout(
    payload: CreateCheckoutRequest,
    db: Session = Depends(get_db),
    user: UserModel = Depends(get_current_user),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    billing_service: BillingService = Depends(get_billing_service),
) -> Dict[str, Any]:
    """Create a subscription checkout session for the selected plan."""
    tenant, role = tenant_context
    if role not in (TenantRole.OWNER, TenantRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant owners and admins can modify subscriptions.",
        )

    try:
        checkout = billing_service.create_subscription_checkout(
            db=db,
            tenant_id=tenant.id,
            user_id=user.id,
            plan_code=payload.plan_code,
        )
        return checkout
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PaymentProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/verify-payment")
def verify_payment_checkout(
    payload: VerifyPaymentRequest,
    db: Session = Depends(get_db),
    user: UserModel = Depends(get_current_user),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    billing_service: BillingService = Depends(get_billing_service),
) -> Dict[str, Any]:
    """Verify checkout payment signature, activate subscription, and allocate credits."""
    tenant, role = tenant_context
    if role not in (TenantRole.OWNER, TenantRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant owners and admins can verify subscription payments.",
        )

    try:
        result = billing_service.verify_and_activate_subscription(
            db=db,
            tenant_id=tenant.id,
            user_id=user.id,
            order_id=payload.order_id,
            payment_id=payload.payment_id,
            signature=payload.signature,
            plan_code=payload.plan_code,
        )
        return result
    except InvalidSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PaymentProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/subscription/cancel")
def cancel_subscription(
    payload: CancelSubscriptionRequest,
    db: Session = Depends(get_db),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    billing_service: BillingService = Depends(get_billing_service),
) -> Dict[str, Any]:
    """Cancel tenant subscription either at period end or immediately."""
    tenant, role = tenant_context
    if role not in (TenantRole.OWNER, TenantRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant owners and admins can cancel subscriptions.",
        )

    sub = billing_service.cancel_tenant_subscription(
        db=db,
        tenant_id=tenant.id,
        immediately=payload.immediately,
    )
    return {
        "status": "cancelled",
        "subscription": sub.to_dict(),
    }


@router.get("/history")
def get_billing_history(
    db: Session = Depends(get_db),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    billing_service: BillingService = Depends(get_billing_service),
) -> Dict[str, Any]:
    """Retrieve billing and invoice history for the tenant."""
    tenant, _ = tenant_context
    history = billing_service.get_billing_history(db, tenant.id)
    return {"history": history}


@router.post("/webhook/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None, alias="X-Razorpay-Signature"),
    db: Session = Depends(get_db),
    billing_service: BillingService = Depends(get_billing_service),
) -> Dict[str, Any]:
    """Handle signed Razorpay webhook notifications."""
    if not x_razorpay_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Razorpay-Signature header.",
        )

    raw_body = await request.body()
    try:
        event_payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        )

    try:
        result = billing_service.process_webhook_event(
            db=db,
            raw_body=raw_body,
            signature=x_razorpay_signature,
            event_payload=event_payload,
        )
        return result
    except InvalidSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing error: {exc}",
        )
