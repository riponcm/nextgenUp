"""Billing and subscription module initialization."""

from demerivo.billing.base import (
    InvalidSignatureError,
    PaymentProvider,
    PaymentProviderError,
)
from demerivo.billing.mock_provider import MockPaymentProvider
from demerivo.billing.plans import PLANS_CATALOG, get_plan, list_plans
from demerivo.billing.razorpay_provider import RazorpayProvider
from demerivo.billing.service import BillingService
from demerivo.billing.stripe_adapter import StripePaymentAdapter
from demerivo.config import Settings, get_settings


def create_payment_provider(settings: Settings) -> PaymentProvider:
    """Instantiate configured payment provider."""
    provider_type = settings.payment_provider.lower()
    if provider_type == "razorpay":
        return RazorpayProvider(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            webhook_secret=settings.razorpay_webhook_secret,
        )
    elif provider_type == "stripe":
        return StripePaymentAdapter(
            secret_key=settings.stripe_secret_key,
            webhook_secret=settings.stripe_webhook_secret,
        )
    return MockPaymentProvider()


def get_billing_service(settings: Settings = None) -> BillingService:
    """Return configured BillingService."""
    cfg = settings or get_settings()
    provider = create_payment_provider(cfg)
    return BillingService(provider=provider)


__all__ = [
    "BillingService",
    "InvalidSignatureError",
    "MockPaymentProvider",
    "PLANS_CATALOG",
    "PaymentProvider",
    "PaymentProviderError",
    "RazorpayProvider",
    "StripePaymentAdapter",
    "create_payment_provider",
    "get_billing_service",
    "get_plan",
    "list_plans",
]
