"""AI Processing Pipeline and Model Registry for Demerivo PixelFix."""

from demerivo.ai.base import (
    AIEngine,
    AIEngineError,
    CommercialLicenseRestrictionError,
    InferenceError,
    ModelNotFoundError,
    UnsupportedOperationError,
)
from demerivo.ai.engine import UnifiedCPUAIEngine, get_ai_engine
from demerivo.ai.registry import OperationCapability, get_operation_registry

__all__ = [
    "AIEngine",
    "AIEngineError",
    "CommercialLicenseRestrictionError",
    "InferenceError",
    "ModelNotFoundError",
    "UnsupportedOperationError",
    "UnifiedCPUAIEngine",
    "get_ai_engine",
    "OperationCapability",
    "get_operation_registry",
]
