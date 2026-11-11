"""Abstract interface and structured exception hierarchy for Demerivo AI processing."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional, Tuple

from demerivo.db.models import OperationType


class AIEngineError(Exception):
    """Base exception for all AI pipeline operations."""
    pass


class ModelNotFoundError(AIEngineError):
    """Requested model file or weights are missing or unreadable."""
    pass


class InferenceError(AIEngineError):
    """Inference execution failed during tensor computation."""
    pass


class UnsupportedOperationError(AIEngineError):
    """Requested operation is not supported by this engine backend."""
    pass


class CommercialLicenseRestrictionError(AIEngineError):
    """Operation or model is restricted by upstream non-commercial licensing."""
    pass


class AIEngine(ABC):
    """Unified contract for image processing and AI enhancement backends."""

    @abstractmethod
    def process(
        self,
        image_bytes: bytes,
        operation_type: OperationType,
        parameters: Optional[Dict[str, Any]] = None,
        progress_cb: Optional[Callable[[str, int], None]] = None,
    ) -> Tuple[bytes, Tuple[int, int]]:
        """Process an image and return (output_png_bytes, (width, height)).
        
        Args:
            image_bytes: Raw binary bytes of the input image.
            operation_type: Target enhancement operation.
            parameters: Optional execution parameters.
            progress_cb: Optional callback taking (stage_name: str, progress_percent: int).
        
        Returns:
            Tuple of (output_png_bytes, (out_width, out_height)).
        """
        pass
