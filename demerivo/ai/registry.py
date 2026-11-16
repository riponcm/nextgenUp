"""Central operation registry, capability definitions, and commercial licensing catalog."""

from typing import Any, Dict, List
from pydantic import BaseModel

from demerivo.db.models import OperationType


class OperationCapability(BaseModel):
    """Metadata describing an AI operation and its infrastructure requirements."""
    operation_type: str
    label: str
    description: str
    base_credits: int
    model_name: str
    license: str
    commercial_cleared: bool
    cpu_compatible: bool
    max_resolution_pixels: int = 4096 * 4096  # 4K UHD (16MP max on CPU)
    supported_parameters: List[str]


OPERATIONS_CATALOG: Dict[OperationType, OperationCapability] = {
    OperationType.UPSCALE_2X: OperationCapability(
        operation_type=OperationType.UPSCALE_2X.value,
        label="2x Super-Resolution",
        description="Double image dimensions with sharp edge reconstruction and noise suppression.",
        base_credits=2,
        model_name="Real-ESRGAN / Lanczos Resampling",
        license="BSD-3-Clause",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=["face_restore"],
    ),
    OperationType.UPSCALE_4X: OperationCapability(
        operation_type=OperationType.UPSCALE_4X.value,
        label="4x Super-Resolution",
        description="Quadruple image resolution, synthesizing fine textures and crisp details up to 4K.",
        base_credits=4,
        model_name="Real-ESRGAN / Multi-stage Lanczos Resampling",
        license="BSD-3-Clause",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=["face_restore"],
    ),
    OperationType.ENHANCE: OperationCapability(
        operation_type=OperationType.ENHANCE.value,
        label="Crystal Clear Enhancement",
        description="Enhance image clarity, dynamic range, and micro-contrast using adaptive histogram equalization.",
        base_credits=1,
        model_name="CLAHE Luminance & Unsharp Mask Detail Synthesis",
        license="BSD-3-Clause",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=["face_restore"],
    ),
    OperationType.BG_REMOVE: OperationCapability(
        operation_type=OperationType.BG_REMOVE.value,
        label="Background Removal",
        description="Isolate primary subject and produce clean transparent alpha or solid background.",
        base_credits=1,
        model_name="BiRefNet-lite / Otsu Morphological Alpha Matting",
        license="MIT / Apache-2.0",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=["background_color"],
    ),
    OperationType.DENOISE: OperationCapability(
        operation_type=OperationType.DENOISE.value,
        label="AI Noise Removal",
        description="Remove sensor noise and low-light grain while preserving true textures using non-local means.",
        base_credits=1,
        model_name="Non-Local Means Adaptive Denoising",
        license="Apache-2.0",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=[],
    ),
    OperationType.DEBLUR: OperationCapability(
        operation_type=OperationType.DEBLUR.value,
        label="Motion & Focus Deblur",
        description="Reconstruct focused sharp edges from camera shake and motion blur.",
        base_credits=1,
        model_name="Laplacian & Deconvolution Edge Reconstruction",
        license="Apache-2.0",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=[],
    ),
    OperationType.COLORIZE: OperationCapability(
        operation_type=OperationType.COLORIZE.value,
        label="Photo Colorization",
        description="Synthesize natural, historically accurate color tones for grayscale imagery.",
        base_credits=1,
        model_name="Chroma Frequency Synthesis",
        license="Apache-2.0",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=[],
    ),
    OperationType.INPAINT: OperationCapability(
        operation_type=OperationType.INPAINT.value,
        label="Object Eraser & Inpaint",
        description="Erase unwanted objects, blemishes, or text with seamless context synthesis.",
        base_credits=1,
        model_name="Fast Marching & Telea Contextual Inpainting",
        license="Apache-2.0",
        commercial_cleared=True,
        cpu_compatible=True,
        max_resolution_pixels=4096 * 4096,
        supported_parameters=["mask_key"],
    ),
}


def get_operation_registry() -> Dict[str, OperationCapability]:
    """Return dictionary of all registered AI capabilities."""
    return {k.value: v for k, v in OPERATIONS_CATALOG.items()}
