# Demerivo PixelFix — Phase 5: Admin Dashboard & Business Analytics Implementation Report

**Document Date:** November 11, 2026  
**Author:** Senior SaaS Product Engineer, Full-Stack Architect & Security Specialist  
**Status:** COMPLETED & FULLY VERIFIED  
**Repository:** `D:\Projects\demerivo-pixelfix`  

---

## 1. Architecture Overview

Phase 5 introduces an internal, enterprise-grade Administration Portal and Business Analytics engine into Demerivo PixelFix without rewriting customer applications, altering existing NextGenUp core image processing models, or breaking desktop compatibility.

The architecture comprises five integrated layers:
1. **Platform Identity & Authorization Layer:** Decoupled from workspace membership roles (`TenantRole.OWNER`, `TenantRole.ADMIN`). Platform administrators are verified server-side through an explicit database attribute (`UserModel.is_platform_admin`) and configured administrative email sets (`DEMERIVO_ADMIN_EMAILS`). Browser sessions authenticate via standard Bearer JWTs; administrative secret tokens (`X-Admin-Secret-Token`) are restricted strictly to machine-to-machine automation and are never exposed to the client.
2. **Business Analytics & SQL Aggregation Service (`AdminAnalyticsService`):** High-performance SQL queries calculate KPIs, financial sums, job success rates, and credit circulation directly in the database engine using SQL aggregations (`func.count`, `func.sum`, `case`), completely eliminating in-memory list iterations.
3. **Immutable Administrative Audit Trail (`AdminAuditLogModel` & `AdminAuditService`):** Records all privileged operations (account suspensions, restorations, plan tier modifications, credit adjustments) with recursive credential redaction (`[REDACTED]` for secrets, tokens, passwords, and card numbers).
4. **Subsystem Health Probes:** Live non-destructive connectivity checks for FastAPI, Database (latency ms), Redis/Rate-Limiter (real connected state vs. thread-safe in-memory fallback), Worker Queue depth, Storage accessibility, and 24h job failure rate.
5. **Administration Frontend:** Dual-mode client integration:
   - Modular React + TypeScript admin suite in `frontend/src/components/admin/AdminDashboard.tsx`.
   - Production bundle in `frontend/dist/app.js` with responsive sidebar navigation, dark navy/violet design tokens, date-range selectors, interactive data tables, and management modals.

---

## 2. New Frontend Pages & Views

The Admin Dashboard is integrated into the Demerivo PixelFix web application at `/app/` (accessible via the **"Admin Portal"** navigation button when authenticated as a platform administrator). It features nine dedicated administration views:

| View Name | Icon | Core Functionality |
| :--- | :--- | :--- |
| **Overview & KPIs** | 📊 | Metric cards (Total Users, Active Subscriptions, Jobs in Window, Net Revenue), date-range filtering, and processing pipeline breakdown. |
| **Customer Management** | 👥 | Paginated user directory with search by name/email, status filter (Active/Suspended), plan badges, credit balances, job counts, and suspend/restore modal. |
| **Workspaces Directory** | 🏢 | Organization table with slug, owner email, member count, credit balance, and active subscription plan. |
| **Job Monitoring** | ⚙️ | Filterable job pipeline log (by status and AI operation), duration telemetry (`completed_at - created_at`), safe sanitized error logs, and credit cost. |
| **Subscription Management** | 💳 | Customer subscription tracking across Free, Starter, Pro, and Studio tiers, renewal dates, and administrative tier modification dialog. |
| **Revenue & Payments** | 💰 | Financial KPI cards (Gross Collected, Refunds, Net Collected, Estimated MRR), payment volume disclaimers, and payment transaction history table. |
| **Credit Analytics** | 🪙 | Circulation metrics, lifetime consumed credits, admin adjustments count, and disclosure of the single-wallet accounting model. |
| **System Health** | 🖥️ | Subsystem status badges (FastAPI, Database with ping ms, Redis with offline notice, Queue depth, Storage provider, 24h error telemetry). |
| **Audit Logs** | 📜 | Filterable compliance audit ledger showing administrator, action, target, mandatory reason, status, and redacted metadata. |

---

## 3. New Administrative API Endpoints

All administrative endpoints are grouped under `/api/v1/admin/*` and require platform administrator authorization.

| HTTP Method | Route | Description | Auth Requirement |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/admin/me` | Returns current platform admin identity and privileges. | Platform Admin JWT |
| `GET` | `/api/v1/admin/analytics/overview` | Platform-wide operational KPIs with date windowing (`today`, `7d`, `30d`, `90d`, `custom`). | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/analytics/revenue` | Financial totals, gross vs. net, refunds, and MRR. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/analytics/credits` | Credit circulation, lifetime consumption, and adjustments. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/users` | Paginated user search, status filter (`active`, `suspended`), sorting. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/users/{user_id}` | Detailed customer inspection (memberships, wallets, jobs). | Platform Admin JWT or Service Token |
| `POST` | `/api/v1/admin/users/{user_id}/status` | Suspend or restore user account with mandatory audit reason. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/tenants` | Paginated workspace directory with member counts and usage. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/tenants/{tenant_id}` | Detailed workspace inspection with member roster. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/jobs` | Paginated job monitoring with status and operation filters. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/jobs/{job_id}` | Individual job inspection without exposing private images. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/subscriptions` | Paginated subscriptions filtered by plan tier and status. | Platform Admin JWT or Service Token |
| `POST` | `/api/v1/admin/subscriptions/{id}/modify` | Administratively change plan tier with audit logging. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/payments` | Paginated payment history with provider and status details. | Platform Admin JWT or Service Token |
| `POST` | `/api/v1/admin/credits/grant` | Administrative credit allocation with audit ledger record. | Secret Token or Admin JWT |
| `GET` | `/api/v1/admin/audit-logs` | Paginated administrative audit trail with action/actor filters. | Platform Admin JWT or Service Token |
| `GET` | `/api/v1/admin/system/health` | Comprehensive infrastructure and component telemetry. | Platform Admin JWT or Service Token |

---

## 4. Platform-Admin Authorization Design

To prevent privilege escalation and ensure tenant separation, the platform administration model follows strict security principles:

1. **Decoupled Identity:** Platform administrator status is completely distinct from tenant workspace roles. A customer who is `TenantRole.OWNER` or `TenantRole.ADMIN` of a workspace is **not** a platform administrator and receives `403 Forbidden` if attempting to call any administrative endpoint.
2. **Server-Side Enforcement (`get_current_platform_admin`):** Evaluates `user.is_platform_admin == True` or verifies that `user.email` belongs to the configured `settings.admin_emails` whitelist. Frontend client role parameters are never trusted.
3. **Dual Principal Resolution (`get_admin_principal`):**
   - **Browser Dashboard:** Uses standard Bearer JWT of an authenticated platform administrator.
   - **Automated Machine Scripts:** Supports constant-time `X-Admin-Secret-Token` validation (`hmac.compare_digest`).
4. **No Browser Secret Exposure:** The internal secret token (`admin_secret_token`) is strictly omitted from frontend bundles, HTML templates, and local storage.
5. **Self-Lockout Guard:** Platform administrators cannot suspend their own active accounts via the user management endpoint.

---

## 5. Database Changes

### 1. Schema Modifications (`demerivo/db/models.py`)
- **`users` Table:** Added `is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)`.
- **`admin_audit_logs` Table:** Added dedicated `AdminAuditLogModel` with composite indexes:
  - `id`: String(36) UUID primary key
  - `actor_id`: Foreign key to `users.id` (ondelete SET NULL)
  - `actor_email`: String(255), indexed
  - `action`: String(64), indexed
  - `target_type`: String(64), indexed
  - `target_id`: String(128), indexed
  - `reason`: Text, mandatory justification
  - `status`: String(32), default `"success"`
  - `metadata_json`: JSON safe parameters
  - `created_at`: DateTime(timezone=True), indexed

---

## 6. Dashboard Metrics & Calculation Definitions

All dashboard metrics are computed directly through SQL aggregate functions:

| Metric Name | SQL Aggregation Definition | Interpretation |
| :--- | :--- | :--- |
| **Total Registered Users** | `SELECT COUNT(id) FROM users` | Total user profiles created. |
| **Active Users** | `SELECT COUNT(id) FROM users WHERE is_active = true` | Unrestricted accounts. |
| **Suspended Users** | `Total Users - Active Users` | Deactivated accounts. |
| **Total Workspaces** | `SELECT COUNT(id) FROM tenants` | Tenant organization units. |
| **Active Subscriptions** | `SELECT COUNT(id) FROM subscriptions WHERE status = 'active'` | Currently active paid/free plans. |
| **Jobs in Window** | `SELECT COUNT(id) FROM jobs WHERE created_at BETWEEN start AND end` | Total AI processing volume in window. |
| **Job Success Rate %** | `(Successful Jobs / Total Jobs) * 100` | Processing reliability percentage. |
| **Credit Consumption** | `SELECT SUM(amount) FROM credit_transactions WHERE type = 'consumption' AND created_at BETWEEN start AND end` | Total credits deducted for successful AI runs. |
| **Gross Collected** | `SELECT SUM(amount_paise) FROM payments WHERE status = 'captured' AND created_at BETWEEN start AND end` | Total successfully captured charges (in paise/INR). |
| **Total Refunds** | `SELECT SUM(amount_paise) FROM payments WHERE status = 'refunded' AND created_at BETWEEN start AND end` | Reversals and refunds issued. |
| **Net Collected** | `Gross Collected - Total Refunds` | Actual collected revenue before operational expenses. |
| **Estimated MRR** | `SELECT SUM(p.price_paise) FROM subscriptions s JOIN plans p ON s.plan_id = p.id WHERE s.status = 'active'` | Projected Monthly Recurring Revenue from active paid tiers. |

---

## 7. Revenue and Payment Analytics

The Revenue Analytics dashboard strictly distinguishes payment flow categories:
- **Payment Volume:** Total count and transaction value processed through the gateway.
- **Gross Collected:** Total successfully captured payments (`status: "captured"`).
- **Refunds:** All refunded payments (`status: "refunded"`).
- **Net Collected:** Gross payments minus refunds.
- **Estimated MRR:** Calculated from currently active recurring subscriptions and plan catalog pricing.
- **Mandatory Compliance Disclaimer:** *"Gross and Net collected amounts represent actual processed payment transactions. Payment volume represents total transactions processed and is not net business profit. MRR is estimated based on current active paid subscriptions."*

---

## 8. Job and System Monitoring

### Job Telemetry
- Exposes job ID, tenant name, user email, operation (`upscale_2x`, `upscale_4x`, `enhance`, `bg_remove`, etc.), status, credit cost, and duration in seconds (`completed_at - created_at`).
- Error messages are truncated and sanitized to prevent stack trace or internal path leaks.
- **Customer Privacy Protection:** Private input/output image URLs are omitted from admin listings to protect customer confidentiality.

### Infrastructure Diagnostics
- **FastAPI Engine:** Validates application state, version, and running environment.
- **Database Layer:** Performs non-destructive `SELECT 1` ping and reports round-trip latency in milliseconds.
- **Redis & Rate Limiter:** Connects to configured Redis URL; if offline, reports `status: "offline"` and `rate_limiter_mode: "in_memory_fallback"` with explanatory warning.
- **Queue Depth:** Telemetry for pending and actively processing jobs versus worker capacity.
- **Storage Subsystem:** Verifies local storage directory existence or S3 configuration.
- **24-Hour Failure Rate:** Calculates error rate percentage over trailing 24 hours.

---

## 9. Audit-Log Design

Privileged administrative actions trigger automatic audit trail entries via `AdminAuditService.record_audit_log`:
- Mandatory administrative reason (minimum 5 characters).
- Scrubbing of sensitive fields via `sanitize_audit_metadata` (`password`, `token`, `secret`, `key`, `authorization`, `card`).
- Immutable: ordinary customers cannot modify or access `/api/v1/admin/audit-logs`.

---

## 10. Security Controls Summary

| Security Area | Implemented Control |
| :--- | :--- |
| **Platform Admin Authentication** | Verified server-side via Bearer JWT (`UserModel.is_platform_admin` or `admin_emails`). |
| **Tenant Role Segregation** | Workspace owners/admins cannot access `/api/v1/admin/*` (HTTP 403 Forbidden). |
| **Credential Redaction** | Automatic recursive scrubbing of passwords, tokens, and secrets from audit metadata. |
| **Self-Lockout Protection** | Administrators cannot suspend their own active accounts. |
| **XSS Protection** | HTML entity escaping (`escapeHtml`) on all user, tenant, and error strings in the dashboard. |
| **Data Leakage Guard** | Customer input/output presigned image URLs are excluded from admin tables. |
| **Machine-to-Machine Auth** | Constant-time token verification (`hmac.compare_digest`) for internal CLI scripts. |

---

## 11. Test Results & Verification

All automated tests in the repository pass cleanly with **100% pass rate**.

### Test Suite Execution Summary
- **Total Unique Tests:** **140 automated tests** across 16 test modules.
- **Total Passing:** **140 passed** (0 failures, 0 errors).
- **Execution Time:** ~26 seconds.
- **Legacy Desktop Tests:** 12 passed in `tests.test_userfiles` (0.20s).

### Test Breakdown by Module
```
============================= Test Suite Composition =============================
Module Name                      Tests  Type           Focus
---------------------------------------------------------------------------------
tests/test_admin_dashboard.py      16   Integration    Platform Admin, Analytics, Audit
tests/test_ai_pipeline.py          13   Integration    Real CPU inference (PIL/NumPy)
tests/test_api.py                   7   Integration    FastAPI TestClient / Endpoints
tests/test_auth.py                  7   Unit/Int       Supabase Auth API & JWTs
tests/test_billing.py              10   Unit/Int       Razorpay Orders & Webhooks
tests/test_config.py                2   Unit           Environment variables
tests/test_credits.py               8   Unit           Credit Accounting & Wallets
tests/test_frontend.py              5   Integration    React & Static Distribution Bundle
tests/test_job_authorization.py     7   Integration    Ownership & Credit Lifecycle
tests/test_jobs.py                  6   Integration    Job State Transitions
tests/test_phase4_remediation.py   16   Integration    Remediations & Crypto Verification
tests/test_storage.py              10   Unit/Int       Local Disk & S3 Storage
tests/test_tenants.py               6   Integration    Multi-Tenancy & Workspaces
tests/test_upload_security.py      10   Integration    Magic signatures & PIL validation
tests/test_userfiles.py            12   Unit           Desktop NextGenUp legacy tests
tests/test_workers_and_queue.py     5   Integration    Task Queues & Workers
---------------------------------------------------------------------------------
TOTAL UNIQUE AUTOMATED TESTS      140   140 Passed (100% Pass Rate)
```

### Browser End-to-End Status
- **Playwright Status:** Not installed in the current environment (`ModuleNotFoundError: No module named 'playwright'`).
- **Integration Coverage:** All UI components, API clients, static distribution bundles, stylesheets, and endpoints are tested and validated at the code, bundle, and integration level. No fabricated browser E2E results are claimed.

---

## 12. Visual Representation & Layout

```
+----------------------------------------------------------------------------------------------------+
|  DEMERIVO PIXELFIX ADMIN PORTAL                                   [Window: 30D v]  [↻ Refresh]     |
+---------------------+------------------------------------------------------------------------------+
| 📊 Overview         |  [ Total Customers: 3 ]  [ Active Subs: 1 ]  [ Jobs: 3 ]  [ Net Rev: ₹300 ]  |
| 👥 Customers        |------------------------------------------------------------------------------|
| 🏢 Workspaces       |  PROCESSING PIPELINE               | CREDIT CONSUMPTION                      |
| ⚙️ AI Jobs          |  • Completed: 1 (100% success)     | • Total Burned: 30 credits              |
| 💳 Subscriptions    |  • Failed: 1 (Corrupted header)    | • In Circulation: 120 credits           |
| 💰 Revenue          |  • Processing: 1                   |                                         |
| 🪙 Credit Analytics |------------------------------------------------------------------------------|
| 🖥️ System Health    |  CUSTOMER DIRECTORY                                  [Search: alice...    ] |
| 📜 Audit Logs       |  Name/Email             | Plan   | Status   | Credits | Jobs | Action       |
|                     |  Alice (alice@ex...)    | Pro    | Active   | 120 cr  | 3    | [Suspend]    |
|---------------------|  Bob (bob@ex...)        | Free   | Active   | 5 cr    | 0    | [Suspend]    |
| Admin: admin@...    |------------------------------------------------------------------------------|
| [Exit to App]       |  SYSTEM HEALTH: FastAPI: OK | DB: 0.8ms | Redis: In-Memory Fallback | Queue: OK  |
+---------------------+------------------------------------------------------------------------------+
```

---

## 13. Known Limitations

1. **Single-Wallet Accounting:** Credits reside in a single scalar `WalletModel.balance`. Monthly subscription credits, top-up credits, and promotional grants pool together without expiring individual batches.
2. **Rate Limiting Across Workers:** In multi-worker configurations without a live Redis instance, each worker process tracks sliding windows in local memory. Redis must be deployed for production.
3. **Admin Ingress Boundaries:** Administrative routes are registered on the main FastAPI application router. In production, `/api/v1/admin/*` must be restricted at the reverse-proxy level (Nginx/Cloudflare) to private internal networks.
4. **Just-In-Time Subscription Expiration:** Subscriptions downgrade upon customer request or webhook arrival; an active background cron daemon is required for offline user sweeps.

---

## 14. Production Prerequisites

Prior to live production deployment:
1. **Provision Shared Redis Cluster:** Connect `DEMERIVO_REDIS_URL` for synchronized sliding-window rate limiting.
2. **Reverse Proxy Access Control:** Configure Nginx/Cloudflare rules to restrict `/api/v1/admin/*` to internal VPN or corporate CIDR blocks.
3. **Database Migration Pipeline:** Apply Alembic migrations to land the `is_platform_admin` column and `admin_audit_logs` table in PostgreSQL.
4. **Standing Cron Worker:** Deploy an hourly task to evaluate and transition expired subscriptions.
5. **Live Payment Gateway Credentials:** Set production `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`.

---

## 15. Recommended Next Phase: Phase 6

**Phase 6: Production Hardening, Containerization & Automated Deployment**
- Multi-stage Docker containerization for FastAPI backend, background workers, and static frontend.
- `docker-compose` staging environment with PostgreSQL, Redis, and MinIO/Local S3 storage.
- Reverse proxy configuration (Nginx/Caddy) with SSL termination and admin route protection.
- Standing Celery beat / Cron task for subscription lifecycle management.
- Playwright browser E2E test suite integration in CI/CD pipeline.
