"""Automated tests for Phase 4 Frontend Architecture, Components, and Static Mounting.

Validates that all React & TypeScript source components exist, the production bundle
in frontend/dist/ is complete and valid, and FastAPI mounts and routes static assets correctly.
"""

import os
import unittest
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from demerivo.api.main import create_app


class TestFrontendArchitectureAndMounting(unittest.TestCase):
    def test_typescript_source_components_exist(self):
        """Verify all modular React/TypeScript source components exist."""
        required_src_files = [
            "frontend/package.json",
            "frontend/tsconfig.json",
            "frontend/index.html",
            "frontend/src/types.ts",
            "frontend/src/api.ts",
            "frontend/src/App.tsx",
            "frontend/src/main.tsx",
            "frontend/src/index.css",
            "frontend/src/components/Navbar.tsx",
            "frontend/src/components/Hero.tsx",
            "frontend/src/components/BeforeAfterSlider.tsx",
            "frontend/src/components/Features.tsx",
            "frontend/src/components/HowItWorks.tsx",
            "frontend/src/components/OperationsShowcase.tsx",
            "frontend/src/components/PricingTable.tsx",
            "frontend/src/components/FAQ.tsx",
            "frontend/src/components/LegalModals.tsx",
            "frontend/src/components/AuthModal.tsx",
            "frontend/src/components/DashboardOverview.tsx",
            "frontend/src/components/PhotoStudio.tsx",
            "frontend/src/components/ImageHistory.tsx",
            "frontend/src/components/WalletView.tsx",
            "frontend/src/components/SubscriptionView.tsx",
            "frontend/src/components/AccountSettings.tsx",
            "frontend/src/components/admin/AdminDashboard.tsx",
        ]
        for rel_path in required_src_files:
            self.assertTrue(os.path.exists(rel_path), f"Source file missing: {rel_path}")

    def test_production_dist_bundle_exists_and_valid(self):
        """Verify frontend/dist contains index.html, styles.css, and app.js."""
        dist_files = [
            "frontend/dist/index.html",
            "frontend/dist/styles.css",
            "frontend/dist/app.js",
        ]
        for rel_path in dist_files:
            self.assertTrue(os.path.exists(rel_path), f"Dist file missing: {rel_path}")
            size = os.path.getsize(rel_path)
            self.assertGreater(size, 200, f"Dist file {rel_path} is too small ({size} bytes)")

    def test_production_stylesheet_tokens(self):
        """Verify styles.css defines core Demerivo PixelFix navy/violet design tokens."""
        with open("frontend/dist/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn("--bg-canvas", css)
        self.assertIn("--bg-surface", css)
        self.assertIn("--primary", css)
        self.assertIn("--border-accent", css)
        self.assertIn("slider-container", css)
        self.assertIn("glass-panel", css)

    def test_production_javascript_bundle_capabilities(self):
        """Verify app.js includes core client logic, sliders, and API integrations."""
        with open("frontend/dist/app.js", "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn("Demerivo PixelFix", js)
        self.assertIn("apiRequest", js)
        self.assertIn("/billing/subscription", js)
        self.assertIn("/jobs/capabilities", js)
        self.assertIn("renderSlider", js)
        self.assertIn("Razorpay", js)
        self.assertIn("renderAdminDashboard", js)
        self.assertIn("/admin/analytics/overview", js)

    def test_fastapi_mounts_and_routes_configured(self):
        """Verify FastAPI registers /app, /static, and root redirect routes."""
        app = create_app()
        route_paths = [getattr(r, "path", None) for r in app.routes if hasattr(r, "path")]

        # Verify mounted paths
        self.assertIn("/app", route_paths)
        self.assertIn("/static", route_paths)
        self.assertIn("/", route_paths)

        # Locate root route and verify it returns a redirect to /app/
        root_route = next(r for r in app.routes if getattr(r, "path", None) == "/")
        endpoint_func = root_route.endpoint
        res = endpoint_func()

        self.assertIsInstance(res, RedirectResponse)
        self.assertEqual(res.headers.get("location"), "/app/")


if __name__ == "__main__":
    unittest.main()
