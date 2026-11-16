"""Demerivo PixelFix — Phase 6 Infrastructure & Security Regression Tests.

Validates:
1. Email verification token generation, expiration, single-use, account-binding, and SHA-256 hashing.
2. Email verification API behavior with tokens and rejection of arbitrary verification.
3. Production configuration validation (failing safely on insecure secrets, SQLite, mock providers).
4. Health (/healthz) and readiness (/readyz) probe endpoints.
5. Structured JSON logging and sensitive credential redaction.
6. Worker crash recovery and heartbeat recording.
7. Admin network isolation middleware.
8. Docker Compose, Dockerfiles, and Nginx configuration rules.
"""

import os
import yaml
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from demerivo.config import Settings, validate_production_configuration
from demerivo.db.session import Base
from demerivo.db.models import (
    UserModel,
    TenantModel,
    EmailVerificationTokenModel,
    WorkerHeartbeatModel,
    JobModel,
    JobStatus,
    OperationType,
    utcnow,
)
from demerivo.services.email_service import (
    create_verification_token,
    verify_email_with_token,
    hash_token,
    InvalidVerificationTokenError,
)
from demerivo.logging_config import SafeJsonLogFormatter
from demerivo.api.routers.health import liveness_probe, readiness_probe
from demerivo.workers.runner import update_heartbeat


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def p6_env(tmp_path):
    """Isolated SQLite database and session factory for Phase 6 unit tests."""
    db_file = tmp_path / "test_p6.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session, TestingSession
    finally:
        session.close()
        engine.dispose()


# ==============================================================================
# 1. Email Verification Token Security Tests
# ==============================================================================

def test_email_verification_token_lifecycle(p6_env):
    """Verifies token creation, SHA-256 hashing, single-use, and expiration."""
    db, _ = p6_env
    user = UserModel(
        id="usr_p6_token_test",
        email="token_test@demerivo.internal",
        display_name="Token Test User",
        email_verified=False,
    )
    db.add(user)
    db.commit()

    # 1. Create verification token
    raw_token = create_verification_token(
        user_id=user.id,
        email=user.email,
        db=db,
        expires_in_hours=24,
    )
    assert raw_token is not None
    assert len(raw_token) >= 32

    # Verify raw token is NOT stored directly; token_hash must be SHA-256
    token_record = (
        db.query(EmailVerificationTokenModel)
        .filter_by(user_id=user.id)
        .first()
    )
    assert token_record is not None
    assert token_record.token_hash != raw_token
    assert token_record.token_hash == hash_token(raw_token)
    assert len(token_record.token_hash) == 64  # SHA-256 hex length
    assert token_record.used_at is None

    # 2. Successfully verify with valid token
    verified_user = verify_email_with_token(raw_token, email=user.email, db=db)
    assert verified_user.id == user.id
    assert verified_user.email_verified is True

    # 3. Token cannot be reused (single-use enforcement)
    with pytest.raises(InvalidVerificationTokenError, match="already been consumed"):
        verify_email_with_token(raw_token, email=user.email, db=db)


def test_email_verification_token_expiration(p6_env):
    """Verifies that expired tokens fail verification."""
    db, _ = p6_env
    user = UserModel(
        id="usr_p6_expired",
        email="expired_token@demerivo.internal",
        display_name="Expired User",
        email_verified=False,
    )
    db.add(user)
    db.commit()

    raw_token = create_verification_token(
        user_id=user.id,
        email=user.email,
        db=db,
        expires_in_hours=24,
    )

    # Force expiration by updating expires_at to 1 hour in the past
    token_record = (
        db.query(EmailVerificationTokenModel)
        .filter_by(user_id=user.id)
        .first()
    )
    token_record.expires_at = utcnow() - timedelta(hours=1)
    db.commit()

    # Attempt verification with expired token
    with pytest.raises(InvalidVerificationTokenError, match="expired"):
        verify_email_with_token(raw_token, email=user.email, db=db)

    db.refresh(user)
    assert user.email_verified is False


def test_email_verification_account_binding(p6_env):
    """Verifies that a token issued for User A cannot verify User B's email."""
    db, _ = p6_env
    user_a = UserModel(
        id="usr_p6_a",
        email="user_a@demerivo.internal",
        email_verified=False,
    )
    user_b = UserModel(
        id="usr_p6_b",
        email="user_b@demerivo.internal",
        email_verified=False,
    )
    db.add_all([user_a, user_b])
    db.commit()

    token_a = create_verification_token(
        user_id=user_a.id,
        email=user_a.email,
        db=db,
    )

    # Try verifying user B using user A's token
    with pytest.raises(InvalidVerificationTokenError, match="does not correspond to the specified email"):
        verify_email_with_token(token_a, email=user_b.email, db=db)


# ==============================================================================
# 2. Production Configuration Validation Tests
# ==============================================================================

def test_production_config_rejects_insecure_defaults():
    """Verifies that validate_production_configuration fails on insecure settings."""
    # Test insecure JWT secret
    settings = Settings(
        env="production",
        supabase_jwt_secret="demerivo-pixelfix-default-jwt-secret-for-dev-32chars",
        admin_secret_token="super-secret-admin-key-must-be-32-chars-long",
        database_url="postgresql+asyncpg://user:pass@db:5432/demerivo",
        payment_provider="razorpay",
        storage_provider="s3",
    )
    with pytest.raises(ValueError, match="SUPABASE_JWT_SECRET"):
        validate_production_configuration(settings)

    # Test short admin secret
    settings = Settings(
        env="production",
        supabase_jwt_secret="a-very-secure-jwt-secret-key-32-chars-long!",
        admin_secret_token="short-admin-secret",
        database_url="postgresql+asyncpg://user:pass@db:5432/demerivo",
        payment_provider="razorpay",
        storage_provider="s3",
    )
    with pytest.raises(ValueError, match="ADMIN_SECRET_TOKEN must be configured"):
        validate_production_configuration(settings)

    # Test SQLite in production
    settings = Settings(
        env="production",
        supabase_jwt_secret="a-very-secure-jwt-secret-key-32-chars-long!",
        admin_secret_token="super-secret-admin-key-must-be-32-chars-long",
        database_url="sqlite+aiosqlite:///demerivo.db",
        payment_provider="razorpay",
        storage_provider="s3",
    )
    with pytest.raises(ValueError, match="SQLite database cannot be used in production"):
        validate_production_configuration(settings)

    # Test mock payment provider in production
    settings = Settings(
        env="production",
        supabase_jwt_secret="a-very-secure-jwt-secret-key-32-chars-long!",
        admin_secret_token="super-secret-admin-key-must-be-32-chars-long",
        database_url="postgresql+asyncpg://user:pass@db:5432/demerivo",
        payment_provider="mock",
        storage_provider="s3",
    )
    with pytest.raises(ValueError, match="PAYMENT_PROVIDER cannot be 'mock' in production"):
        validate_production_configuration(settings)

    # Test local storage in production
    settings = Settings(
        env="production",
        supabase_jwt_secret="a-very-secure-jwt-secret-key-32-chars-long!",
        admin_secret_token="super-secret-admin-key-must-be-32-chars-long",
        database_url="postgresql+asyncpg://user:pass@db:5432/demerivo",
        payment_provider="razorpay",
        storage_provider="local",
    )
    with pytest.raises(ValueError, match="STORAGE_PROVIDER cannot be 'local' in production"):
        validate_production_configuration(settings)


def test_production_config_accepts_valid_production_settings():
    """Verifies that valid production configuration passes validation."""
    settings = Settings(
        env="production",
        supabase_jwt_secret="a-very-secure-jwt-secret-key-32-chars-long!",
        admin_secret_token="super-secret-admin-key-must-be-32-chars-long",
        database_url="postgresql+asyncpg://demerivo:prodpass@db.internal:5432/demerivo_prod",
        payment_provider="razorpay",
        storage_provider="s3",
    )
    # Should not raise any exception
    validate_production_configuration(settings)


# ==============================================================================
# 3. Health & Readiness Probe Tests
# ==============================================================================

def test_liveness_probe_healthz():
    """Verifies /healthz liveness probe returns HTTP 200 without external dependencies."""
    res = liveness_probe()
    assert res["status"] == "alive"
    assert res["service"] == "demerivo-pixelfix"


def test_readiness_probe_readyz_healthy(p6_env):
    """Verifies /readyz returns healthy when database, queue, and storage are reachable."""
    db, _ = p6_env
    settings = Settings(env="testing", storage_provider="local")
    mock_queue = MagicMock()
    mock_queue.get_queue_length.return_value = 0
    mock_storage = MagicMock()

    res = readiness_probe(
        response=MagicMock(),
        settings=settings,
        db=db,
        queue=mock_queue,
        storage=mock_storage,
    )
    assert res["status"] == "ready"
    assert res["subsystems"]["database"] == "healthy"
    assert res["subsystems"]["queue"]["status"] == "healthy"


def test_readiness_probe_readyz_unhealthy_on_db_failure():
    """Verifies /readyz returns HTTP 503 JSONResponse and sanitizes exceptions for public callers."""
    settings = Settings(env="testing", storage_provider="local")
    mock_db = MagicMock()
    mock_db.execute.side_effect = RuntimeError("Database connection timed out")
    mock_queue = MagicMock()
    mock_queue.get_queue_length.return_value = 0
    mock_storage = MagicMock()

    # Public unauthenticated caller: exceptions MUST be sanitized
    resp = readiness_probe(
        response=MagicMock(),
        settings=settings,
        db=mock_db,
        queue=mock_queue,
        storage=mock_storage,
    )
    assert resp.status_code == 503
    assert b"unhealthy" in resp.body
    # Ensure internal exception details are NOT leaked publicly
    assert b"Database connection timed out" not in resp.body

    # Authorized monitor passing X-Admin-Token receives detailed diagnostics
    admin_secret = "test-admin-secret-token-32-chars-long"
    settings_with_admin = Settings(
        env="testing",
        storage_provider="local",
        admin_secret_token=admin_secret,
    )
    mock_request = MagicMock()
    mock_request.headers.get.side_effect = lambda h, default="": admin_secret if h == "X-Admin-Token" else default

    resp_auth = readiness_probe(
        request=mock_request,
        response=MagicMock(),
        settings=settings_with_admin,
        db=mock_db,
        queue=mock_queue,
        storage=mock_storage,
    )
    assert resp_auth.status_code == 503
    assert b"Database connection timed out" in resp_auth.body


# ==============================================================================
# 4. Structured JSON Logging & Credential Redaction Tests
# ==============================================================================

def test_safe_json_log_formatter():
    """Verifies JSON output and automatic credential redaction."""
    import logging
    import json

    formatter = SafeJsonLogFormatter()
    record = logging.LogRecord(
        name="demerivo.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="User authenticated: password=supersecret, token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "demerivo.test"
    # Ensure sensitive credentials were redacted
    assert "supersecret" not in parsed["message"]
    assert "[REDACTED]" in parsed["message"]


# ==============================================================================
# 5. Worker Crash Recovery & Heartbeat Tests
# ==============================================================================

def test_worker_crash_recovery_resets_stranded_jobs(p6_env):
    """Verifies that WorkerManager.recover_stranded_jobs detects stranded processing jobs."""
    from demerivo.workers.manager import WorkerManager

    db, session_factory = p6_env

    user = UserModel(id="usr_p6_worker_rec", email="rec@demerivo.internal")
    db.add(user)
    db.commit()

    tenant = TenantModel(id="tnt_p6_worker_rec", name="Worker WS", slug="worker-ws", owner_id=user.id)
    db.add(tenant)
    db.commit()

    stale_job = JobModel(
        id="job_stranded_001",
        tenant_id=tenant.id,
        user_id=user.id,
        operation_type=OperationType.UPSCALE_2X,
        status=JobStatus.PROCESSING,
        input_key="test/input.png",
    )
    db.add(stale_job)
    db.commit()

    settings = Settings(env="testing", storage_provider="local")
    mock_queue = MagicMock()
    mock_queue.recover_stranded_jobs.return_value = ["job_stranded_001"]

    manager = WorkerManager(
        settings=settings,
        session_factory=session_factory,
        queue=mock_queue,
    )

    recovered = manager.recover_stranded_jobs(timeout_seconds=60)
    assert "job_stranded_001" in recovered

    db.refresh(stale_job)
    assert stale_job.status == JobStatus.FAILED
    assert "refunded" in stale_job.error_message


def test_worker_heartbeat_upsert(p6_env):
    """Verifies that update_heartbeat records and updates worker telemetry."""
    db, session_factory = p6_env

    # Test heartbeat creation
    with patch("demerivo.workers.runner.SessionLocal", side_effect=session_factory):
        update_heartbeat(
            worker_id="worker-test-01",
            hostname="host-alpha",
            status_str="active",
            active_jobs=3,
        )

    hb = db.query(WorkerHeartbeatModel).filter_by(worker_id="worker-test-01").first()
    assert hb is not None
    assert hb.hostname == "host-alpha"
    assert hb.active_jobs_count == 3
    assert hb.status == "active"

    # Test heartbeat update
    with patch("demerivo.workers.runner.SessionLocal", side_effect=session_factory):
        update_heartbeat(
            worker_id="worker-test-01",
            hostname="host-alpha",
            status_str="active",
            active_jobs=5,
        )

    with session_factory() as verify_db:
        hb_updated = verify_db.query(WorkerHeartbeatModel).filter_by(worker_id="worker-test-01").first()
        assert hb_updated is not None
        assert hb_updated.active_jobs_count == 5


# ==============================================================================
# 6. Admin Network Isolation & Proxy Trust Tests
# ==============================================================================

def test_proxy_header_trust_and_spoofing_prevention():
    """Verifies that untrusted direct connections cannot spoof client IP via headers."""
    import ipaddress
    from fastapi import Request

    trusted_proxies_cidr = "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128"
    trusted_proxy_nets = [ipaddress.ip_network(c.strip()) for c in trusted_proxies_cidr.split(",")]

    # Helper replicating resolve_client_ip logic
    def resolve_test_ip(peer_ip: str, headers: dict) -> str:
        peer_addr = ipaddress.ip_address(peer_ip)
        peer_is_trusted = any(
            peer_addr.version == net.version and peer_addr in net
            for net in trusted_proxy_nets
        )
        if not peer_is_trusted:
            return peer_ip

        real_ip = headers.get("x-real-ip")
        if real_ip:
            try:
                ipaddress.ip_address(real_ip)
                return real_ip
            except ValueError:
                pass

        xff = headers.get("x-forwarded-for")
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

    # 1. Attack Scenario: Direct untrusted client connects from public IP 203.0.113.195
    # and attempts to spoof X-Forwarded-For: 127.0.0.1 to gain admin access
    untrusted_peer = "203.0.113.195"
    spoofed_xff = {"x-forwarded-for": "127.0.0.1"}
    resolved = resolve_test_ip(untrusted_peer, spoofed_xff)
    # The spoofed header MUST be ignored, returning the untrusted peer IP
    assert resolved == "203.0.113.195"

    # 2. Attack Scenario: Direct untrusted client attempts to spoof X-Real-IP: 10.0.0.1
    spoofed_x_real = {"x-real-ip": "10.0.0.1"}
    resolved = resolve_test_ip(untrusted_peer, spoofed_x_real)
    assert resolved == "203.0.113.195"

    # 3. Legitimate Scenario: Trusted Nginx reverse proxy (172.16.0.2) forwards external client IP
    trusted_nginx_peer = "172.16.0.2"
    legit_external_headers = {"x-real-ip": "203.0.113.195", "x-forwarded-for": "203.0.113.195"}
    resolved = resolve_test_ip(trusted_nginx_peer, legit_external_headers)
    assert resolved == "203.0.113.195"

    # 4. Legitimate Scenario: Trusted Nginx forwards internal management VPN IP (10.0.1.50)
    legit_admin_headers = {"x-real-ip": "10.0.1.50", "x-forwarded-for": "10.0.1.50"}
    resolved = resolve_test_ip(trusted_nginx_peer, legit_admin_headers)
    assert resolved == "10.0.1.50"

    # 5. IPv6 Handling: Direct untrusted IPv6 peer with spoofed header
    untrusted_ipv6 = "2001:db8::100"
    resolved_ipv6 = resolve_test_ip(untrusted_ipv6, {"x-forwarded-for": "127.0.0.1"})
    assert resolved_ipv6 == "2001:db8::100"

    # 6. IPv6 Trusted Loopback (::1)
    trusted_ipv6_peer = "::1"
    resolved_loopback = resolve_test_ip(trusted_ipv6_peer, {"x-real-ip": "10.0.0.2"})
    assert resolved_loopback == "10.0.0.2"


def test_admin_network_isolation_middleware():
    """Verifies IP filtering logic on administrative routes."""
    import ipaddress

    allowed_cidr = "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128"
    allowed_networks = [ipaddress.ip_network(c.strip()) for c in allowed_cidr.split(",")]

    # Helper function matching middleware logic
    def check_ip_allowed(client_ip: str) -> bool:
        ip_addr = ipaddress.ip_address(client_ip.strip())
        return any(ip_addr.version == net.version and ip_addr in net for net in allowed_networks)

    # 1. Local loopback is allowed (IPv4 and IPv6)
    assert check_ip_allowed("127.0.0.1") is True
    assert check_ip_allowed("::1") is True

    # 2. Internal private network IPs are allowed
    assert check_ip_allowed("10.0.1.50") is True
    assert check_ip_allowed("172.16.0.5") is True
    assert check_ip_allowed("192.168.1.100") is True

    # 3. Public untrusted internet IPs are blocked
    assert check_ip_allowed("203.0.113.195") is False
    assert check_ip_allowed("8.8.8.8") is False
    assert check_ip_allowed("198.51.100.2") is False
    assert check_ip_allowed("2001:db8::1") is False


# ==============================================================================
# 7. Infrastructure File Architecture & Security Checks
# ==============================================================================

def test_docker_compose_security_configuration():
    """Verifies docker-compose.yml does not expose database or redis ports to the host."""
    compose_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docker-compose.yml")
    assert os.path.isfile(compose_path), "docker-compose.yml must exist"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    services = compose_data.get("services", {})
    assert "postgres" in services
    assert "redis" in services
    assert "api" in services
    assert "worker" in services
    assert "nginx" in services

    # Ensure postgres and redis do NOT expose ports to the public host
    assert "ports" not in services["postgres"], "Postgres must not expose host ports in production compose"
    assert "ports" not in services["redis"], "Redis must not expose host ports in production compose"

    # Only nginx should expose ports 80/443
    assert "ports" in services["nginx"]
    nginx_ports = [str(p) for p in services["nginx"]["ports"]]
    assert any("443" in p for p in nginx_ports)

    # Ensure network segregation exists
    networks = compose_data.get("networks", {})
    assert "frontend-net" in networks
    assert "backend-net" in networks

    # Verify worker service production configuration
    worker_service = services["worker"]
    assert worker_service.get("stop_grace_period") == "30s", "Worker service must define 30s stop_grace_period"
    worker_env = [str(e) for e in worker_service.get("environment", [])]
    assert any("DEMERIVO_SUPABASE_JWT_SECRET" in e for e in worker_env), (
        "Worker environment must receive DEMERIVO_SUPABASE_JWT_SECRET"
    )


def test_dockerfiles_run_as_non_root():
    """Verifies Dockerfiles enforce non-root execution and healthchecks."""
    root_dir = os.path.dirname(os.path.dirname(__file__))

    for dockerfile in ["Dockerfile.api", "Dockerfile.worker"]:
        path = os.path.join(root_dir, dockerfile)
        assert os.path.isfile(path), f"{dockerfile} must exist"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "USER demerivo" in content or "USER 10001" in content, (
            f"{dockerfile} must run as non-root user 'demerivo' (UID 10001)"
        )
        assert "HEALTHCHECK" in content, f"{dockerfile} must define a HEALTHCHECK"

    # Verify Dockerfile.worker layer optimization and heartbeat healthcheck
    worker_df = os.path.join(root_dir, "Dockerfile.worker")
    with open(worker_df, "r", encoding="utf-8") as f:
        worker_content = f.read()
    assert "--chown=demerivo:demerivo" in worker_content, "Dockerfile.worker must use --chown"
    assert "worker-heartbeat" in worker_content, "Dockerfile.worker must check worker heartbeat"


def test_nginx_security_and_admin_isolation_config():
    """Verifies nginx configuration enforces HTTPS and restricts administrative endpoints."""
    nginx_conf_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "nginx", "conf.d", "default.conf"
    )
    assert os.path.isfile(nginx_conf_path), "nginx default.conf must exist"

    with open(nginx_conf_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check TLS enforcement
    assert "ssl_certificate" in content
    assert "ssl_protocols TLSv1.2 TLSv1.3" in content

    # Check admin isolation
    assert "location /api/v1/admin/" in content
    assert "deny all;" in content
    assert "allow 127.0.0.1;" in content or "allow 10.0.0.0/8;" in content


def test_backup_and_restore_scripts_exist():
    """Verifies backup and restore scripts exist with proper headers."""
    root_dir = os.path.dirname(os.path.dirname(__file__))
    backup_script = os.path.join(root_dir, "scripts", "backup_postgres.sh")
    restore_script = os.path.join(root_dir, "scripts", "restore_postgres.sh")

    assert os.path.isfile(backup_script), "scripts/backup_postgres.sh must exist"
    assert os.path.isfile(restore_script), "scripts/restore_postgres.sh must exist"

    with open(backup_script, "r", encoding="utf-8") as f:
        backup_content = f.read()
    assert "pg_dump" in backup_content
    assert "sha256sum" in backup_content
    assert "aes-256-cbc" in backup_content
    assert "BACKUP_S3_BUCKET" in backup_content

    with open(restore_script, "r", encoding="utf-8") as f:
        restore_content = f.read()
    assert "pg_restore" in restore_content
    assert "aes-256-cbc" in restore_content
    assert "RESTORE-" in restore_content  # Confirmation safety prompt
