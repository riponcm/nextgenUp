"""Credit wallet balance and transaction ledger endpoints."""

from typing import List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import get_current_tenant, get_current_user
from demerivo.credits.wallet_service import get_wallet_service
from demerivo.db.models import TenantModel, TenantRole, UserModel
from demerivo.db.session import get_db

router = APIRouter(prefix="/api/v1/credits", tags=["Credit Accounting & Wallets"])


class CreditBalanceResponse(BaseModel):
    """Wallet balance representation."""
    tenant_id: str
    workspace_name: str
    balance: int


class CreditTransactionResponse(BaseModel):
    """Credit audit ledger entry."""
    id: str
    tenant_id: str
    wallet_id: str
    user_id: Optional[str] = None
    job_id: Optional[str] = None
    transaction_type: str
    amount: int
    balance_after: int
    idempotency_key: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None


@router.get(
    "/balance",
    response_model=CreditBalanceResponse,
    summary="Get current active workspace credit balance",
)
def get_balance(
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    """Return available integer credits for the active tenant workspace."""
    tenant, _ = tenant_context
    wallet_service = get_wallet_service()
    wallet = wallet_service.get_wallet(tenant.id, db)
    return CreditBalanceResponse(
        tenant_id=tenant.id,
        workspace_name=tenant.name,
        balance=wallet.balance,
    )


@router.get(
    "/transactions",
    response_model=List[CreditTransactionResponse],
    summary="Retrieve immutable credit transaction ledger",
)
def list_transactions(
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Return audit history of all credit grants, reservations, consumptions, and refunds."""
    tenant, _ = tenant_context
    wallet_service = get_wallet_service()
    records = wallet_service.get_transactions(tenant.id, db, limit=limit, offset=offset)
    return [
        CreditTransactionResponse(
            id=r.id,
            tenant_id=r.tenant_id,
            wallet_id=r.wallet_id,
            user_id=r.user_id,
            job_id=r.job_id,
            transaction_type=r.transaction_type.value,
            amount=r.amount,
            balance_after=r.balance_after,
            idempotency_key=r.idempotency_key,
            description=r.description,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in records
    ]
