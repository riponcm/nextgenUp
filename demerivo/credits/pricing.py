"""Central credit pricing policy for Demerivo PixelFix operations."""

from typing import Any, Dict, Optional

from demerivo.config import Settings, get_settings
from demerivo.db.models import OperationType


class CreditPricingPolicy:
    """Calculates integer credit requirements based on operation type and processing parameters."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def calculate_cost(
        self,
        operation_type: OperationType,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Determine required credit cost for a requested enhancement task.
        
        Rules:
        - Background removal: 1 credit
        - Basic enhancement: 1 credit
        - 2x upscale: 2 credits
        - 4x upscale: 4 credits
        - Face restoration: +2 credits add-on
        - Denoise / Deblur / Colorize / Inpaint: 1 credit
        """
        params = parameters or {}
        cost = 0

        # Base operation costs
        if operation_type == OperationType.BG_REMOVE:
            cost = self.settings.credit_cost_bg_remove
        elif operation_type == OperationType.ENHANCE:
            cost = self.settings.credit_cost_enhance
        elif operation_type == OperationType.UPSCALE_2X:
            cost = self.settings.credit_cost_upscale_2x
        elif operation_type == OperationType.UPSCALE_4X:
            cost = self.settings.credit_cost_upscale_4x
        elif operation_type in (
            OperationType.DENOISE,
            OperationType.DEBLUR,
            OperationType.COLORIZE,
            OperationType.INPAINT,
        ):
            cost = 1
        else:
            cost = 1

        # Check for Face Restoration add-on
        if params.get("face_restore") is True or params.get("restore_faces") is True:
            cost += self.settings.credit_cost_face_restore

        return cost


_pricing_policy_instance: Optional[CreditPricingPolicy] = None


def get_pricing_policy() -> CreditPricingPolicy:
    """Return singleton instance of CreditPricingPolicy."""
    global _pricing_policy_instance
    if _pricing_policy_instance is None:
        _pricing_policy_instance = CreditPricingPolicy()
    return _pricing_policy_instance
