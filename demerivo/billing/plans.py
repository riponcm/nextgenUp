"""Centralized Subscription Plans Catalog for Demerivo PixelFix.

All prices, credit allocations, and feature entitlements are authoritatively
governed server-side here and synchronized into the database.
"""

from typing import Any, Dict, List
from pydantic import BaseModel


class PlanDefinition(BaseModel):
    """Immutable definition of a Demerivo PixelFix subscription plan."""
    code: str
    name: str
    description: str
    price_paise: int
    monthly_credits: int
    currency: str = "INR"
    features: List[str]

    @property
    def price_inr(self) -> float:
        return self.price_paise / 100.0


# Authoritative Plan Catalog
PLANS_CATALOG: Dict[str, PlanDefinition] = {
    "free": PlanDefinition(
        code="free",
        name="Free Explorer",
        description="Try our AI photo enhancement suite with free monthly credits.",
        price_paise=0,
        monthly_credits=5,
        currency="INR",
        features=[
            "5 Monthly Credits",
            "Upscale 2x & 4x",
            "Background Removal",
            "Standard CPU Queue",
            "Up to 2K Resolution",
            "Community Support",
        ]
    ),
    "starter": PlanDefinition(
        code="starter",
        name="Starter Creator",
        description="Ideal for content creators and casual photographers.",
        price_paise=19900,  # ₹199 / month
        monthly_credits=40,
        currency="INR",
        features=[
            "40 Monthly Credits",
            "All AI Operations (Enhance, Denoise, Deblur)",
            "Background Removal (Alpha & Solid Color)",
            "Priority CPU Processing",
            "High Definition Output (Up to 4K)",
            "Standard Email Support",
        ]
    ),
    "pro": PlanDefinition(
        code="pro",
        name="Pro Photographer",
        description="Designed for professional photographers and digital artists.",
        price_paise=49900,  # ₹499 / month
        monthly_credits=150,
        currency="INR",
        features=[
            "150 Monthly Credits",
            "All AI Operations + Colorization & Inpainting",
            "High-Throughput Parallel Processing",
            "Ultra HD 4K Output",
            "Priority Support (24h response)",
            "Commercial Usage License",
        ]
    ),
    "studio": PlanDefinition(
        code="studio",
        name="Studio Enterprise",
        description="Maximum volume and concurrency for studios and creative agencies.",
        price_paise=99900,  # ₹999 / month
        monthly_credits=400,
        currency="INR",
        features=[
            "400 Monthly Credits",
            "All AI Enhancement Operations",
            "Maximum Concurrent Worker Execution",
            "Batch Processing Workflow",
            "Dedicated Account Manager",
            "Commercial Usage License",
        ]
    ),
}


def get_plan(plan_code: str) -> PlanDefinition:
    """Retrieve plan definition by code, raising KeyError if unknown."""
    code_lower = plan_code.lower()
    if code_lower not in PLANS_CATALOG:
        raise KeyError(f"Unknown plan code: '{plan_code}'. Valid plans: {list(PLANS_CATALOG.keys())}")
    return PLANS_CATALOG[code_lower]


def list_plans() -> List[Dict[str, Any]]:
    """Return all active plans formatted for public API reflection."""
    return [
        {
            "code": p.code,
            "name": p.name,
            "description": p.description,
            "price_paise": p.price_paise,
            "price_inr": p.price_inr,
            "monthly_credits": p.monthly_credits,
            "currency": p.currency,
            "features": p.features,
        }
        for p in PLANS_CATALOG.values()
    ]
