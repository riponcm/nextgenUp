"""Automated tests for AI inference pipeline, image operations, and licensing restrictions."""

import io
import time
import pytest
from PIL import Image

from demerivo.ai.base import (
    CommercialLicenseRestrictionError,
    InferenceError,
    UnsupportedOperationError,
)
from demerivo.ai.engine import UnifiedCPUAIEngine
from demerivo.ai.registry import OPERATIONS_CATALOG, get_operation_registry
from demerivo.db.models import OperationType


def make_sample_png(width: int = 64, height: int = 64, color: str = "red") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestAIPipelineOperations:
    """Test all supported operations with real image processing on CPU."""

    def setup_method(self):
        self.engine = UnifiedCPUAIEngine()
        self.sample_bytes = make_sample_png(64, 64, color="blue")

    def test_upscale_2x(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.UPSCALE_2X)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (128, 128)
        assert len(out_bytes) > 0
        img = Image.open(io.BytesIO(out_bytes))
        assert img.size == (128, 128)
        print(f"\n[Benchmark] UPSCALE_2X (64x64 -> 128x128): {elapsed_ms:.2f} ms")

    def test_upscale_4x(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.UPSCALE_4X)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (256, 256)
        assert len(out_bytes) > 0
        img = Image.open(io.BytesIO(out_bytes))
        assert img.size == (256, 256)
        print(f"\n[Benchmark] UPSCALE_4X (64x64 -> 256x256): {elapsed_ms:.2f} ms")

    def test_enhance_maintains_original_resolution(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.ENHANCE)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (64, 64)
        img = Image.open(io.BytesIO(out_bytes))
        assert img.size == (64, 64)
        print(f"\n[Benchmark] ENHANCE (64x64): {elapsed_ms:.2f} ms")

    def test_background_removal_transparent(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.BG_REMOVE)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (64, 64)
        img = Image.open(io.BytesIO(out_bytes))
        assert img.mode == "RGBA"
        print(f"\n[Benchmark] BG_REMOVE transparent: {elapsed_ms:.2f} ms")

    def test_background_removal_custom_color(self):
        out_bytes, dims = self.engine.process(
            self.sample_bytes,
            OperationType.BG_REMOVE,
            parameters={"background_color": "#FFFFFF"},
        )
        assert dims == (64, 64)
        img = Image.open(io.BytesIO(out_bytes))
        assert img.mode == "RGB"

    def test_denoise(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.DENOISE)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (64, 64)
        print(f"\n[Benchmark] DENOISE: {elapsed_ms:.2f} ms")

    def test_deblur(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.DEBLUR)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (64, 64)
        print(f"\n[Benchmark] DEBLUR: {elapsed_ms:.2f} ms")

    def test_colorize(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.COLORIZE)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (64, 64)
        img = Image.open(io.BytesIO(out_bytes))
        assert img.mode == "RGB"
        print(f"\n[Benchmark] COLORIZE: {elapsed_ms:.2f} ms")

    def test_inpaint(self):
        t0 = time.perf_counter()
        out_bytes, dims = self.engine.process(self.sample_bytes, OperationType.INPAINT)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert dims == (64, 64)
        print(f"\n[Benchmark] INPAINT: {elapsed_ms:.2f} ms")

    def test_commercial_license_gate_gfpgan(self):
        """GFPGAN face restoration MUST be blocked for commercial SaaS usage."""
        with pytest.raises(CommercialLicenseRestrictionError) as exc_info:
            self.engine.process(
                self.sample_bytes,
                OperationType.UPSCALE_4X,
                parameters={"face_restore": True},
            )
        assert "disabled for commercial usage" in str(exc_info.value)

    def test_invalid_corrupted_image(self):
        corrupted = b"NOT_A_REAL_IMAGE_BYTES_12345"
        with pytest.raises(InferenceError):
            self.engine.process(corrupted, OperationType.UPSCALE_2X)

    def test_too_small_image(self):
        tiny = make_sample_png(4, 4)
        with pytest.raises(InferenceError) as exc_info:
            self.engine.process(tiny, OperationType.UPSCALE_2X)
        assert "dimensions are too small" in str(exc_info.value)

    def test_operation_registry_catalog(self):
        registry = get_operation_registry()
        assert "upscale_2x" in registry
        assert "upscale_4x" in registry
        assert "enhance" in registry
        assert "bg_remove" in registry
        assert "denoise" in registry
        assert "deblur" in registry
        assert "colorize" in registry
        assert "inpaint" in registry

        # Verify commercial clearance flags
        assert registry["upscale_2x"].commercial_cleared is True
        assert registry["bg_remove"].commercial_cleared is True
        assert registry["upscale_4x"].base_credits == 4
        assert registry["bg_remove"].base_credits == 1
