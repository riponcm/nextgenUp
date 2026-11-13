"""Environment and application configuration for Demerivo PixelFix."""

import os
from functools import lru_cache
from typing import Literal, Set
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for Demerivo PixelFix.
    
    Reads from environment variables with DEMERIVO_ prefix or .env file.
    """
    model_config = SettingsConfigDict(
        env_prefix="DEMERIVO_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Environment
    env: Literal["development", "testing", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Server network settings
    host: str = "0.0.0.0"
    port: int = 8000

    # Persistence / Database
    database_url: str = "sqlite:///./demerivo.db"

    # Storage provider selection
    storage_provider: Literal["local", "s3"] = "local"
    local_storage_dir: str = "./storage_data"

    # Cloudflare R2 / S3 configuration
    s3_endpoint_url: str = ""
    s3_bucket_name: str = "demerivo-pixelfix-media"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"
    s3_presigned_expiration_seconds: int = 3600

    # Security & upload constraints
    max_upload_size_bytes: int = 20 * 1024 * 1024  # 20 MB
    max_image_width: int = 8192
    max_image_height: int = 8192
    max_image_pixels: int = 50_000_000  # 50 MP (Decompression bomb guard)

    allowed_image_extensions: Set[str] = {
        ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"
    }
    allowed_mime_types: Set[str] = {
        "image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff"
    }

    # Worker concurrency & timeout controls
    max_concurrent_workers: int = 2
    job_timeout_seconds: int = 600
    redis_url: str = "redis://localhost:6379/0"
    queue_backend: Literal["auto", "redis", "local"] = "auto"

    # Supabase Authentication & JWT Configuration
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = "demerivo-pixelfix-default-jwt-secret-for-dev-32chars"
    jwt_algorithm: str = "HS256"
    jwt_audience: str = "authenticated"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Credit System & Default Pricing Policies
    free_tier_initial_credits: int = 5
    credit_cost_bg_remove: int = 1
    credit_cost_enhance: int = 1
    credit_cost_upscale_2x: int = 2
    credit_cost_upscale_4x: int = 4
    credit_cost_face_restore: int = 2

    # Administrative Secret Token for Internal Operations
    admin_secret_token: str = ""
    admin_emails: str = "admin@demerivo.com"

    # Rate Limiting
    rate_limit_requests_per_minute: int = 60
    rate_limit_auth_requests_per_minute: int = 10
    rate_limit_upload_requests_per_minute: int = 30
    rate_limit_jobs_requests_per_minute: int = 30

    # Subscription Billing & Payment Gateways
    payment_provider: Literal["mock", "razorpay", "stripe"] = "mock"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Subscription Plan Credits (Monthly)
    free_plan_monthly_credits: int = 5
    starter_plan_monthly_credits: int = 40
    pro_plan_monthly_credits: int = 150
    studio_plan_monthly_credits: int = 400

    # Subscription Plan Prices (INR in Paise: 1 INR = 100 Paise)
    free_plan_price_paise: int = 0
    starter_plan_price_paise: int = 19900  # ₹199
    pro_plan_price_paise: int = 49900      # ₹499
    studio_plan_price_paise: int = 99900   # ₹999

    # Worker Concurrency & Multi-Process Controls
    start_embedded_workers: bool = True

    # Email & Notification Provider Configuration
    email_provider: Literal["none", "console", "mock", "smtp"] = "none"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from_address: str = "noreply@demerivo.com"
    email_from_name: str = "Demerivo PixelFix"

    # Administrative Network Isolation (CIDR & Reverse Proxy Boundaries)
    admin_allowed_cidr: str = "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128"
    admin_enforce_network_isolation: bool = False
    trusted_proxies: str = "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_testing(self) -> bool:
        return self.env == "testing"

    def validate_production_configuration(self) -> None:
        """Enforce strict production safeguards against default secrets, mock payments, and SQLite."""
        if not self.is_production:
            return

        DEFAULT_DEV_SECRET = "demerivo-pixelfix-default-jwt-secret-for-dev-32chars"
        if not self.supabase_jwt_secret or self.supabase_jwt_secret == DEFAULT_DEV_SECRET or len(self.supabase_jwt_secret) < 32:
            raise ValueError(
                "Production configuration error: SUPABASE_JWT_SECRET must be explicitly set to a "
                "cryptographically secure secret with at least 32 characters in production."
            )

        if not self.admin_secret_token or len(self.admin_secret_token) < 32:
            raise ValueError(
                "Production configuration error: ADMIN_SECRET_TOKEN must be configured with a "
                "minimum of 32 characters in production."
            )

        if "sqlite" in self.database_url.lower():
            raise ValueError(
                "Production configuration error: SQLite database cannot be used in production. "
                "A PostgreSQL database connection URL (postgresql://...) is required."
            )

        if self.payment_provider == "mock":
            raise ValueError(
                "Production configuration error: PAYMENT_PROVIDER cannot be 'mock' in production. "
                "Must configure razorpay sandbox credentials."
            )

        if self.storage_provider == "local":
            raise ValueError(
                "Production configuration error: STORAGE_PROVIDER cannot be 'local' in production. "
                "Cloud object storage ('s3' or 'r2') is required."
            )


def validate_production_configuration(settings: Settings | None = None) -> None:
    """Module-level helper to validate production configuration."""
    if settings is None:
        settings = get_settings()
    settings.validate_production_configuration()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
