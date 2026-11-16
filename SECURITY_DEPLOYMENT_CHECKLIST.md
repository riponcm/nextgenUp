# Demerivo PixelFix — Pre-Production Security Deployment Checklist

This checklist must be verified and signed off before launching Demerivo PixelFix into staging or production environments.

---

## 1. Secrets & Cryptographic Hygiene

- [x] **No Default Secrets in Production:** `validate_production_configuration()` verifies `DEMERIVO_JWT_SECRET` is not set to default development values.
- [x] **Secret Entropy:** Both `DEMERIVO_JWT_SECRET` and `DEMERIVO_ADMIN_SECRET` are at least 32 characters of high-entropy randomness.
- [x] **No Hardcoded Secrets in Code or Dockerfiles:** Git repository and Docker images are completely free of embedded API keys or passwords.
- [x] **Environment File Permissions:** `.env` file permissions on production hosts are locked down (`chmod 600 .env`).
- [x] **Safe JSON Logging:** `SafeJsonLogFormatter` redacts passwords, tokens, API keys, and JWT signatures in stdout and container logs.

---

## 2. Infrastructure & Container Hardening

- [x] **Non-Root Execution:** `Dockerfile.api` and `Dockerfile.worker` enforce execution under the non-root user `demerivo` (UID 10001).
- [x] **No Public Database or Cache Exposure:** `postgres` (port 5432) and `redis` (port 6379) publish zero ports to the host system in `docker-compose.yml`.
- [x] **Network Segregation:** Containers communicate across isolated internal Docker bridge networks (`frontend-net` and `backend-net`).
- [x] **Container Health Probes:** All services define automated healthchecks (`/healthz` for API, process check for worker, `pg_isready` for postgres, `redis-cli ping` for redis).
- [x] **Graceful Shutdown:** Worker and API processes listen for `SIGINT` and `SIGTERM` to complete in-flight tasks and close database connections cleanly.

---

## 3. Network & Edge Gateway (Nginx)

- [x] **Mandatory TLS:** All cleartext HTTP traffic on port 80 is permanently redirected (`301 Moved Permanently`) to HTTPS on port 443.
- [x] **Modern SSL/TLS Protocols:** SSLv3, TLS 1.0, and TLS 1.1 are disabled; only TLS 1.2 and TLS 1.3 are enabled with forward secrecy ciphers.
- [x] **Security Headers Configured:**
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
  - `X-Frame-Options: SAMEORIGIN`
  - `X-Content-Type-Options: nosniff`
  - `X-XSS-Protection: 1; mode=block`
  - `Content-Security-Policy: default-src 'self' ...`
- [x] **Edge Rate Limiting:** Separate rate-limiting zones protect authentication endpoints (10 r/m), image uploads (30 r/m), and general API traffic (60 r/m).
- [x] **Administrative Network Isolation (H-2 Resolution):** `/api/v1/admin/*` is restricted at the edge gateway to trusted CIDR subnets (`deny all;` for external IPs).

---

## 4. Authentication, Authorization & Multi-Tenancy

- [x] **Cryptographic Email Verification:** Email verification tokens are single-use, 32+ character CSPRNG tokens, stored as SHA-256 hashes, with explicit 24-hour expiration.
- [x] **No Arbitrary Email Verification:** Calling `/api/v1/auth/verify-email` without a valid token fails with HTTP 422/400.
- [x] **Platform Administrator Distinction:** Platform admins require `is_platform_admin == True` and matching `DEMERIVO_ADMIN_EMAILS`. Ordinary workspace owners cannot access admin endpoints.
- [x] **Suspended Administrator Rejection:** Every administrative API call verifies active user status in the database; suspended accounts lose access immediately regardless of JWT expiry.
- [x] **Self-Lockout Prevention:** The final remaining active platform administrator cannot deactivate or delete their own account.
- [x] **Tenant Scoping:** All customer job and billing operations strictly enforce tenant boundaries via `tenant_id` foreign keys and authorization checks.

---

## 5. Billing, Payments & Marketing Claims

- [x] **Live Mode Disabled:** Razorpay live payments are explicitly deactivated; only test sandbox credentials are used until legal approval.
- [x] **Signature Verification:** All Razorpay payment captures and webhook payloads verify HMAC SHA-256 signatures before allocating credits.
- [x] **Webhook Idempotency:** Duplicate payment webhook deliveries are detected and prevented from granting credits more than once.
- [x] **No Unsupported 8K Claims:** Marketing copy and UI capabilities accurately present supported 2x/4x resolutions; unsupported 8K claims remain disabled.

---

## 6. Backup & Disaster Recovery

- [x] **Automated Backup Script:** `scripts/backup_postgres.sh` creates compressed, timestamped custom dumps with SHA-256 integrity checksums.
- [x] **Retention Policy:** Automated pruning deletes backups older than 30 days.
- [x] **Tested Restoration Workflow:** `scripts/restore_postgres.sh` validates SHA-256 checksums and requires user confirmation before modifying the database.
- [x] **Offsite Storage Synchronization:** Procedures documented for synchronizing local dumps to encrypted cloud object storage buckets.

---

## 7. CI/CD & Deployment Pipelines

- [x] **Pinned GitHub Actions:** All GitHub Actions in `.github/workflows/ci-cd.yml` use full 40-character commit SHAs.
- [x] **Least-Privilege Token Permissions:** Workflows define `permissions: contents: read`.
- [x] **Gated Deployment Environments:** Production deployments are gated by the `production` environment with manual approval requirements.
- [x] **Automated Regression Suite:** All 174 automated tests run in CI prior to container builds.
- [x] **Desktop App Compatibility:** `tests.test_userfiles` runs in CI to guarantee the original NextGenUp desktop application remains untouched and functional.

---

## Sign-Off

| Role | Name | Signature | Date |
| :--- | :--- | :--- | :--- |
| **Lead DevOps / Infrastructure Engineer** | | ____________________ | ____ / ____ / ________ |
| **Principal Security Architect** | | ____________________ | ____ / ____ / ________ |
| **Product Engineering Lead** | | ____________________ | ____ / ____ / ________ |
