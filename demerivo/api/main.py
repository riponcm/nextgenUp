"""Demerivo PixelFix — FastAPI application entry point."""

import ipaddress
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from demerivo.config import get_settings
from demerivo.db.session import init_db
from demerivo.jobs.dispatcher import get_dispatcher
from demerivo.security.upload_validator import UploadValidationError
from demerivo.storage.base import StorageError, FileNotFoundStorageError, InvalidPathStorageError
from demerivo.jobs.service import JobNotFoundError, InvalidJobStateTransition
from demerivo.credits.wallet_service import InsufficientCreditsError, WalletNotFoundError
from demerivo.tenants.service import (
    TenantNotFoundError,
    TenantPermissionDeniedError,
    MemberAlreadyExistsError,
)
from demerivo.auth.provider import InvalidCredentialsError, UserAlreadyExistsError
from demerivo.auth.jwt import InvalidTokenAuthError, TokenExpiredAuthError

from demerivo.workers.manager import get_worker_manager

from demerivo.api.routers.health import router as health_router
from demerivo.api.routers.auth import router as auth_router
from demerivo.api.routers.profile import router as profile_router
from demerivo.api.routers.tenants import router as tenants_router
from demerivo.api.routers.credits import router as credits_router
from demerivo.api.routers.uploads import router as uploads_router
from demerivo.api.routers.jobs import router as jobs_router
from demerivo.api.routers.storage_local import router as storage_local_router
from demerivo.api.routers.billing import router as billing_router
from demerivo.api.routers.admin import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager initializing database and worker pool."""
    settings = get_settings()
    # Validate production configuration safeguards
    settings.validate_production_configuration()

    # Initialize database tables
    init_db()

    worker_manager = None
    if settings.env != "testing" and settings.start_embedded_workers:
        # Start persistent background worker manager
        worker_manager = get_worker_manager(settings)
        worker_manager.start()

    yield

    # Graceful shutdown of workers
    if worker_manager is not None:
        worker_manager.stop()


def create_app() -> FastAPI:
    """Application factory for Demerivo PixelFix API."""
    settings = get_settings()

    app = FastAPI(
        title="Demerivo PixelFix API",
        description="Scalable Cloud AI Photo Enhancement SaaS Backend with Multi-Tenancy & Credit Accounting",
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS Middleware configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["https://demerivo.com", "https://app.demerivo.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID Middleware (Observability)
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response

    # Admin Network Isolation & Trusted Proxy Middleware (Defense-in-Depth for Finding H-2 & M-1)
    def resolve_client_ip(request: Request) -> str:
        """Resolve client IP strictly validating trusted reverse proxies.
        
        If request.client.host is NOT in settings.trusted_proxies, forwarding headers
        (X-Forwarded-For, X-Real-IP) are completely ignored to prevent IP spoofing.
        """
        peer_ip = request.client.host if request.client and request.client.host else "127.0.0.1"
        try:
            peer_addr = ipaddress.ip_address(peer_ip)
        except ValueError:
            return peer_ip

        trusted_proxy_nets = [
            ipaddress.ip_network(c.strip())
            for c in settings.trusted_proxies.split(",")
            if c.strip()
        ]
        peer_is_trusted = any(
            peer_addr.version == net.version and peer_addr in net
            for net in trusted_proxy_nets
        )

        if not peer_is_trusted:
            # Direct client is not a trusted proxy: ignore all forwarding headers
            return peer_ip

        # Peer is a trusted proxy: check X-Real-IP first
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            real_ip = real_ip.strip()
            try:
                ipaddress.ip_address(real_ip)
                return real_ip
            except ValueError:
                pass

        # Fallback to right-most untrusted hop in X-Forwarded-For
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            for hop in reversed(hops):
                try:
                    hop_addr = ipaddress.ip_address(hop)
                    if not any(hop_addr.version == net.version and hop_addr in net for net in trusted_proxy_nets):
                        return hop
                except ValueError:
                    continue

        return peer_ip

    @app.middleware("http")
    async def admin_network_isolation_middleware(request: Request, call_next):
        if settings.admin_enforce_network_isolation and request.url.path.startswith("/api/v1/admin/"):
            ip_str = resolve_client_ip(request)
            allowed_cidrs = [
                ipaddress.ip_network(c.strip())
                for c in settings.admin_allowed_cidr.split(",")
                if c.strip()
            ]
            try:
                client_addr = ipaddress.ip_address(ip_str)
                is_allowed = any(
                    client_addr.version == cidr.version and client_addr in cidr
                    for cidr in allowed_cidrs
                )
                if not is_allowed:
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Administrative access blocked: client IP is outside the allowed management network."},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Administrative access blocked: invalid client network origin."},
                )
        return await call_next(request)

    # Custom Exception Handlers
    @app.exception_handler(InsufficientCreditsError)
    async def insufficient_credits_handler(request: Request, exc: InsufficientCreditsError):
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content={"error": "InsufficientCreditsError", "detail": str(exc)},
        )

    @app.exception_handler(TenantPermissionDeniedError)
    async def tenant_permission_handler(request: Request, exc: TenantPermissionDeniedError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": "TenantPermissionDeniedError", "detail": str(exc)},
        )

    @app.exception_handler(TenantNotFoundError)
    async def tenant_not_found_handler(request: Request, exc: TenantNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "TenantNotFoundError", "detail": str(exc)},
        )

    @app.exception_handler(MemberAlreadyExistsError)
    async def member_exists_handler(request: Request, exc: MemberAlreadyExistsError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "MemberAlreadyExistsError", "detail": str(exc)},
        )

    @app.exception_handler(UserAlreadyExistsError)
    async def user_exists_handler(request: Request, exc: UserAlreadyExistsError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "UserAlreadyExistsError", "detail": str(exc)},
        )

    @app.exception_handler(InvalidCredentialsError)
    async def invalid_credentials_handler(request: Request, exc: InvalidCredentialsError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "InvalidCredentialsError", "detail": str(exc)},
        )

    @app.exception_handler(TokenExpiredAuthError)
    async def token_expired_handler(request: Request, exc: TokenExpiredAuthError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "TokenExpiredAuthError", "detail": str(exc)},
        )

    @app.exception_handler(InvalidTokenAuthError)
    async def invalid_token_handler(request: Request, exc: InvalidTokenAuthError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "InvalidTokenAuthError", "detail": str(exc)},
        )

    @app.exception_handler(UploadValidationError)
    async def upload_validation_handler(request: Request, exc: UploadValidationError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "UploadValidationError", "detail": str(exc)},
        )

    @app.exception_handler(JobNotFoundError)
    async def job_not_found_handler(request: Request, exc: JobNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "JobNotFoundError", "detail": str(exc)},
        )

    @app.exception_handler(InvalidJobStateTransition)
    async def invalid_transition_handler(request: Request, exc: InvalidJobStateTransition):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "InvalidJobStateTransition", "detail": str(exc)},
        )

    @app.exception_handler(InvalidPathStorageError)
    async def storage_path_error_handler(request: Request, exc: InvalidPathStorageError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": "InvalidPathStorageError", "detail": str(exc)},
        )

    @app.exception_handler(FileNotFoundStorageError)
    async def storage_file_not_found_handler(request: Request, exc: FileNotFoundStorageError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "FileNotFoundStorageError", "detail": str(exc)},
        )

    # Register Routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(profile_router)
    app.include_router(tenants_router)
    app.include_router(credits_router)
    app.include_router(uploads_router)
    app.include_router(jobs_router)
    app.include_router(storage_local_router)
    app.include_router(billing_router)
    app.include_router(admin_router)

    # Static Assets & Frontend Web Application
    if os.path.exists("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    if os.path.exists("frontend/dist"):
        dist_dir = os.path.abspath("frontend/dist")
        index_html_path = os.path.join(dist_dir, "index.html")

        @app.get("/app/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            """SPA route handler: serves static file if it exists, otherwise falls back to index.html."""
            safe_path = os.path.abspath(os.path.join(dist_dir, full_path.lstrip("/\\")))
            if safe_path.startswith(dist_dir) and os.path.isfile(safe_path):
                return FileResponse(safe_path)
            return FileResponse(index_html_path)

        @app.get("/app", include_in_schema=False)
        @app.get("/app/", include_in_schema=False)
        async def app_root():
            return FileResponse(index_html_path)

        @app.get("/", include_in_schema=False)
        def root_redirect():
            return RedirectResponse(url="/app/")

    return app


app = create_app()
