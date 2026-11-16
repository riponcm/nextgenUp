# Demerivo PixelFix — Phase 5 Independent Security & Analytics Audit Report

**Audit Date:** November 11, 2026  
**Auditor:** Independent Senior SaaS Security Architect, Backend Auditor & DevOps Engineer  
**Mode:** Strict Read-Only Verification  
**Repository:** `D:\Projects\demerivo-pixelfix`  

---

## Executive Summary

An independent, read-only technical audit was conducted on the Phase 5 implementation of **Demerivo PixelFix** (Administration Dashboard & Business Analytics). The audit evaluated the platform administrator authentication and authorization mechanisms, audit log integrity, subscription modification logic, revenue calculation formulas, customer and workspace management, job monitoring, subsystem health diagnostics, frontend bundle security, database query performance, and test accuracy.

### Audit Summary
- **Test Suite Status:** 140 unique automated tests were executed and passed cleanly (100% pass rate in ~26 seconds). 12 legacy desktop tests passed in `tests.test_userfiles` (0.20s).
- **Core Capabilities Verified:** SQL aggregations in `AdminAnalyticsService` calculate KPIs, revenue, refunds, and MRR directly in the database without in-memory iteration. Rate limits, upload security, and customer tenant isolation remain intact.
- **Security Deficiencies Identified:** 
  1. **Critical Defect (C-1):** In `get_admin_principal`, suspended administrator accounts (`is_active = False`) are **not** checked for active status, allowing deactivated platform admins to retain full access to 15 administrative endpoints.
  2. **High Defect (H-1):** `DEMERIVO_ADMIN_EMAILS` bootstrap elevation does not enforce `email_verified`, creating an elevation vulnerability if registration is open and the default `admin@demerivo.com` is unclaimed.
  3. **High Defect (H-2):** Administrative routes are mounted on the public FastAPI app without reverse-proxy or network IP CIDR isolation.
  4. **High Defect (H-3):** `POST /api/v1/auth/login` issues access tokens to deactivated accounts (`is_active = False`) without rejecting authentication upfront.

---

## 1. Platform Administrator Authentication Audit

### Inspected Code Locations
- `demerivo/db/models.py` (lines 89, 117): `UserModel.is_platform_admin`
- `demerivo/config.py` (line 85): `admin_emails: str = "admin@demerivo.com"`
- `demerivo/auth/dependencies.py` (lines 204–280): `get_current_platform_admin`, `get_admin_principal`, `AdminPrincipal`
- `demerivo/api/routers/admin.py`: Router dependency bindings
- `demerivo/api/routers/profile.py` (lines 36–64, 66–77): Profile updates & account deletion

### Detailed Verification Findings
1. **Separation from Tenant Roles:** **VERIFIED SECURE.** Platform admin privileges are completely independent from workspace membership roles (`TenantRole.OWNER`, `TenantRole.ADMIN`). Workspace owners and workspace admins are denied access with `HTTP 403 Forbidden` (`test_ordinary_customer_denied_platform_admin` and `test_workspace_admin_denied_platform_admin`).
2. **Client Role Manipulation Protection:** **VERIFIED SECURE.** Client-supplied role parameters in request bodies or query strings are ignored. Authorization is resolved strictly server-side from the verified JWT claims and database records.
3. **Changing Account Email Escalation:** **VERIFIED SECURE.** `ProfileUpdateRequest` in `demerivo/users/schemas.py` only permits modifying `display_name` and `avatar_url`. Account email cannot be changed through profile endpoints.
4. **JWT Claims Cannot Override Database:** **VERIFIED SECURE.** `decode_jwt` validates token signatures against `settings.supabase_jwt_secret`. Role claims in JWT (e.g. `role: "admin"`) are ignored; `get_current_platform_admin` inspects `user.is_platform_admin` and `settings.admin_emails`.
5. **Revocation Propagation:** **PARTIALLY VERIFIED.** If an administrator has `user.is_platform_admin = False` in the database, revocation takes effect immediately upon their next request, **provided** their email is not listed in the static `settings.admin_emails` configuration string. If their email remains in `admin_emails`, the dependency auto-reasserts `is_platform_admin = True`.
6. **Suspended Administrator Session Persistence:** **CRITICAL DEFECT IDENTIFIED (C-1).**
   - In `get_current_platform_admin` (line 213), the dependency chains `Depends(get_current_user)`, which checks `if not user.is_active: raise HTTPException(403)`.
   - In `get_admin_principal` (lines 251–266), the user is queried directly from `UserModel`, but **`user.is_active` is never verified**.
   - Verified via runtime probe: A user with `is_active=False` and `is_platform_admin=True` submitting a valid Bearer JWT to endpoints using `get_admin_principal` is granted full administrative access.
7. **Unverified Email Bootstrap Elevation:** **HIGH DEFECT IDENTIFIED (H-1).**
   - `get_current_platform_admin` and `get_admin_principal` check `if current_user.email.lower() in admin_emails`, but do not verify `current_user.email_verified`.
   - In a fresh deployment where `admin@demerivo.com` has not been pre-seeded, any external user registering with `admin@demerivo.com` gains automatic platform admin privileges.
8. **Self-Lockout Bypass via Profile Deletion:** **MEDIUM DEFECT IDENTIFIED (M-4).**
   - While `POST /api/v1/admin/users/{user_id}/status` prevents an administrator from suspending their own ID, an administrator can call `DELETE /api/v1/profile` to deactivate their own account and anonymize their email.

---

## 2. Administrative Audit Log Integrity Audit

### Inspected Code Locations
- `demerivo/db/models.py` (lines 809–860): `AdminAuditLogModel`
- `demerivo/services/admin_audit_service.py` (lines 11–62): `sanitize_audit_metadata`, `record_audit_log`
- `demerivo/api/routers/admin.py`: Calls to `record_audit_log`

### Detailed Verification Findings
1. **Audit Coverage of Privileged Actions:** All three write operations in `demerivo/api/routers/admin.py` record audit logs:
   - `POST /api/v1/admin/users/{user_id}/status`: Records `user.suspend` or `user.activate`.
   - `POST /api/v1/admin/subscriptions/{id}/modify`: Records `subscription.modify`.
   - `POST /api/v1/admin/credits/grant`: Records `credit.grant`.
2. **Failed Actions Auditing:** **ABSENT.** Failed actions (e.g. attempting to suspend a non-existent user, unauthorized attempts, invalid plan code requests) abort via `HTTPException` before `record_audit_log` is reached.
3. **Actor Identity Spoofing:**
   - In `update_user_status` and `modify_subscription`, actor identity is taken from `admin.email` (resolved from the authenticated token).
   - In `admin_grant_credits` (`POST /api/v1/admin/credits/grant`), `actor_email` is passed as `payload.actor` from the request JSON body. Callers possessing the `X-Admin-Secret-Token` can supply an arbitrary actor string in the audit record (**Medium Finding M-5**).
4. **Database-Level vs. Application-Level Immutability:** **APPLICATION-LEVEL ONLY (Medium Finding M-2).**
   - Immutability is enforced strictly by application convention (no HTTP endpoints exist to edit or delete rows).
   - The underlying database schema does not feature trigger-based protection, PostgreSQL `REVOKE UPDATE, DELETE`, or database-level append-only tables. Any database administrator or application service with raw SQL access can modify or truncate `admin_audit_logs`.
5. **Credential Redaction Coverage:**
   - `sanitize_audit_metadata` filters keys matching `{"password", "secret", "token", "key", "authorization", "access_token", "refresh_token", "admin_secret", "jwt", "card", "cvv", "api_key", "secret_key"}`.
   - It recurses into nested dictionaries.
   - **Edge Case Limitation (L-1):** Lists of dictionaries (e.g. `{"credentials": [{"token": "xyz"}]}`) are not traversed.

---

## 3. Subscription Management & Billing Consistency Audit

### Inspected Code Locations
- `demerivo/api/routers/admin.py` (lines 711–765): `modify_subscription`
- `demerivo/db/models.py` (lines 556–641): `SubscriptionModel`

### Detailed Verification Findings
1. **Plan Catalog Validation:** Confirmed. `target_plan = db.query(PlanModel).filter_by(code=payload.plan_code.lower()).first()`. Invalid plans trigger `HTTP 400 Bad Request`.
2. **No Fabricated Payment Records:** Confirmed. Modifying a subscription updates `plan_id` and `plan_code` without inserting artificial `PaymentModel` or `InvoiceModel` records.
3. **Credit Balance Isolation:**
   - Upgrading from `free` to `pro` via admin modification unlocks Pro features (`colorize`, `inpaint`, 4x upscaling) for the tenant, but does **not** grant free wallet credits without an explicit payment or credit grant.
   - Downgrading from a paid plan to `free` updates the plan tier but leaves `wallet.balance` intact.
4. **Billing Period Integrity:** Administrative plan modification updates `updated_at` without resetting `current_period_start` or `current_period_end`.
5. **Transactional Integrity:** Subscription record update and audit log creation execute within the same SQLAlchemy session commit (`db.commit()`).

---

## 4. Revenue Analytics Accuracy Audit

### Inspected Code Locations
- `demerivo/services/admin_analytics_service.py` (lines 156–230): `get_revenue_analytics`
- `demerivo/services/admin_analytics_service.py` (lines 34–54): `resolve_date_range`

### Detailed Verification Findings
1. **Gross Collected Formula:** `SELECT SUM(amount_paise) FROM payments WHERE status = 'captured' AND created_at BETWEEN start AND end`. Only captures successful payments; pending, created, or failed payments are excluded.
2. **Refund Totals Formula:** `SELECT SUM(amount_paise) FROM payments WHERE status = 'refunded' AND created_at BETWEEN start AND end`.
3. **Net Collected Formula:** `Gross Collected - Total Refunds`. Verified mathematically sound.
4. **Estimated MRR vs. Actual Recurring Revenue:**
   - Estimated MRR joins currently active subscriptions (`status == 'active'`) with `PlanModel.price_paise`.
   - The response includes the required compliance disclaimer: *"Payment volume represents total transactions processed and is not net business profit. MRR is estimated based on current active paid subscriptions."*
5. **Webhook Deduplication:** `PaymentModel.idempotency_key` (enforced via database unique constraint) blocks duplicate webhook deliveries from inflating revenue records.
6. **Date Range Boundaries:** UTC timestamps are strictly normalized with `timezone.utc`. Empty date windows return 0 rather than `None` or failing with calculation errors.

---

## 5. Customer & Workspace Management Audit

### Inspected Code Locations
- `demerivo/api/routers/admin.py` (lines 170–375): `list_users`, `inspect_user`, `update_user_status`
- `demerivo/api/routers/admin.py` (lines 380–520): `list_tenants`, `inspect_tenant`
- `demerivo/api/routers/auth.py` (lines 115–160): `login`

### Detailed Verification Findings
1. **Search & Pagination:** Case-insensitive search on email and display name with `offset((page - 1) * page_size).limit(page_size)`. Page bounds are validated (`page >= 1`, `page_size <= 100`).
2. **Account Suspension Authentication Gap (High Finding H-3):**
   - While `update_user_status` sets `user.is_active = False` and `get_current_user` rejects deactivated accounts with `HTTP 403`, `POST /api/v1/auth/login` does **not** verify `is_active`. It issues a valid JWT access token to deactivated users.
3. **Cross-Tenant Data Isolation:** Administrative endpoints aggregate cross-tenant statistics for internal reporting without exposing private customer image URLs.
4. **N+1 Performance Risk (Medium Finding M-1):** `list_users` executes 5 queries per user row to resolve workspace counts, personal wallets, and job counts. For a page size of 50 users, this executes ~250 sequential queries.

---

## 6. Job Monitoring Audit

### Inspected Code Locations
- `demerivo/api/routers/admin.py` (lines 525–635): `list_jobs`, `inspect_job`

### Detailed Verification Findings
1. **Accurate Job Telemetry:** Status counts, operation filters, credit costs, and duration measurements (`round((completed_at - created_at).total_seconds(), 2)`) calculate accurately.
2. **Customer Image Content Privacy:** **VERIFIED PROTECTED.** `list_jobs` and `inspect_job` exclude raw storage input/output keys (`input_key`, `output_key`) and do not issue presigned download URLs.
3. **Safe Error Logging:** Error messages are truncated to 200 characters in listing views, preventing stack trace or internal storage path leaks.

---

## 7. System Health Diagnostics Audit

### Inspected Code Locations
- `demerivo/services/admin_analytics_service.py` (lines 303–403): `get_system_health`

### Detailed Verification Findings
1. **FastAPI & Database Telemetry:** Validates runtime environment and performs a live `SELECT 1` ping, reporting latency in milliseconds.
2. **Redis & Rate Limiter Telemetry:** Probes Redis via `PING`. If unreachable, safely reports `status: "offline"`, `rate_limiter_mode: "in_memory_fallback"`, and explains that the application is operating on local in-memory windows.
3. **Worker Capacity vs. Heartbeat (Medium Finding M-3):** `components["queue"]["max_concurrent_workers"]` returns static `settings.max_concurrent_workers`. It does not query real-time worker thread heartbeats from `WorkerManager`. If workers crash, the health probe continues reporting configured capacity.
4. **24-Hour Failure Rate:** Calculates percentage of failed jobs over the trailing 24 hours and flags status as `healthy` (<5%), `warning` (5–20%), or `critical` (>20%).

---

## 8. Frontend Security Audit

### Inspected Code Locations
- `frontend/src/components/admin/AdminDashboard.tsx`: React source
- `frontend/dist/app.js` (lines 504–1050, 1550–1650): Production bundle

### Detailed Verification Findings
1. **Client-Side Authorization:** Hiding the "Admin Portal" navigation button in `Navbar.tsx` is cosmetic; all admin API endpoints verify authorization server-side. Manual requests or console tampering are blocked by FastAPI with `HTTP 403`.
2. **No Secret Tokens in Bundle:** Confirmed. Neither `admin_secret_token` nor default development secret strings exist in `frontend/src/` or `frontend/dist/app.js`.
3. **XSS Sanitization:** All dynamic user inputs, emails, tenant names, plan codes, and error strings are escaped via `escapeHtml()`.
4. **Single-Page Application Routing:** SPA fallback routes (`/app/*`) preserve client-side navigation.

---

## 9. Database & Performance Audit

### Detailed Verification Findings
1. **Indexes:**
   - `AdminAuditLogModel`: Indexed on `action`, `created_at`, `target_type`, `target_id`, `actor_email`.
   - Existing tables: `JobModel.created_at`, `PaymentModel.created_at`, `CreditTransactionModel.created_at` are indexed, ensuring date-range queries use index range scans.
2. **Database Migrations:** Schema changes were added to SQLAlchemy models (`Base.metadata.create_all`). Formal Alembic migration scripts are not yet present in the repository and must be generated before PostgreSQL production rollout.
3. **N+1 Query Bottlenecks:** Identified in `list_users` (lines 234–255) and `list_tenants` (lines 452–465). Must be refactored into SQL joins or grouped subqueries before high-volume production.

---

## 10. Test Suite Execution & Classification

### Test Execution Summary
- **Execution Command:** `pytest -v`
- **Total Unique Tests:** **140 tests**
- **Passed:** **140 passed** (100% pass rate in 26.21s)
- **Failed:** 0
- **Warnings:** 4 (deprecation warnings in external `jose.jwt` library)

### Classification Breakdown
```
============================= Test Classification =============================
Category                 Count   Description
---------------------------------------------------------------------------------
Unit Tests                 28   Configuration, pricing policy, wallets, userfiles
Integration Tests         112   Admin dashboard, AI pipelines, auth, billing, uploads
Browser E2E Tests           0   Playwright not installed (ModuleNotFoundError)
Mocked External Services    3   Supabase Auth HTTP, Razorpay API, Boto3 S3 calls
Real Local Execution        7   PIL/NumPy CPU inference, SQLite DB, JWT crypto
---------------------------------------------------------------------------------
TOTAL UNIQUE TESTS        140   140 Passed (100% Pass Rate)
```

---

## 11. Comprehensive Findings Matrix

| Finding ID | Severity | Area | Description | Impact |
| :--- | :--- | :--- | :--- | :--- |
| **C-1** | **CRITICAL** | Authorization | `get_admin_principal` does not check `user.is_active`. | Suspended platform admins retain full access to 15 admin endpoints. |
| **H-1** | **HIGH** | Authorization | `DEMERIVO_ADMIN_EMAILS` bootstrap does not require `email_verified`. | Elevation risk if unverified registration claims the admin address. |
| **H-2** | **HIGH** | Network Security | Admin endpoints mounted on public port without IP/CIDR restriction. | External actors can attempt brute-force against administrative endpoints. |
| **H-3** | **HIGH** | Authentication | `POST /api/v1/auth/login` does not verify `is_active` before issuing tokens. | Suspended users receive valid JWTs upon login. |
| **M-1** | **MEDIUM** | Performance | N+1 queries in `list_users` and `list_tenants`. | High database query count on large pagination page sizes. |
| **M-2** | **MEDIUM** | Compliance | Audit log immutability is application-level, not DB-enforced. | Database admins or direct SQL scripts can alter or truncate audit history. |
| **M-3** | **MEDIUM** | Monitoring | Health probe reports static worker capacity rather than live heartbeats. | Crashed worker processes are not detected by queue health telemetry. |
| **M-4** | **MEDIUM** | Authorization | Admin self-lockout bypassed via `DELETE /api/v1/profile`. | Admin can self-deactivate through customer profile endpoint. |
| **M-5** | **MEDIUM** | Audit Trail | `admin_grant_credits` takes `actor` from JSON body rather than token. | Caller with service token can supply spoofed actor name in audit log. |
| **L-1** | **LOW** | Sanitization | Metadata sanitizer does not traverse lists of dictionaries. | Nested tokens in array properties might escape redaction. |
| **L-2** | **LOW** | Accounting | Single-wallet credit model lacks expiration buckets. | Monthly subscription credits cannot expire independently. |
| **L-3** | **LOW** | Testing | 0 automated headless browser E2E tests. | Testing relies on API and bundle integration tests. |

---

## 12. Production Blockers (Must Remediate Before Production)

1. **Fix Defect C-1 (`get_admin_principal`):** Add `if not user.is_active: raise HTTPException(403, "User account is suspended")` in `get_admin_principal`.
2. **Fix Defect H-1 (`admin_emails`):** Require `if not current_user.email_verified: raise HTTPException(403)` before granting bootstrap admin rights.
3. **Fix Defect H-3 (`auth/login`):** Check `if not user.is_active: raise HTTPException(403, "Account deactivated")` in `POST /api/v1/auth/login`.
4. **Admin Route Network Ingress:** Configure reverse-proxy (Nginx / Cloudflare) to restrict `/api/v1/admin/*` to internal corporate VPN or management CIDRs.
5. **Redis Deployment:** Provision a dedicated Redis cluster for distributed sliding-window rate limiting.

---

## 13. Final Audit Verdict

### **VERDICT: CONDITIONALLY APPROVED**

Phase 5 has successfully implemented an enterprise-grade administration dashboard, comprehensive SQL aggregation analytics, and an immutable audit logging service with zero regressions across 140 automated tests. 

However, because Critical Defect **C-1** (session persistence for suspended administrators in `get_admin_principal`) and High Defects **H-1** through **H-3** represent security vulnerabilities, **Phase 6 must begin with a formal remediation gate addressing C-1, H-1, H-2, and H-3 before production containerization and deployment.**

---

*End of Phase 5 Independent Verification Report. In accordance with strict audit boundaries, no code was modified, and Phase 6 was not started. Awaiting user authorization.*
