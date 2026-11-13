# DEMERIVO PIXELFIX — PHASE 4 INDEPENDENT VERIFICATION AUDIT REPORT
**Auditor:** Senior SaaS Architect, Security Auditor & Principal Verification Engineer  
**Target Repository:** `D:\Projects\demerivo-pixelfix`  
**Base Repository:** `https://github.com/riponcm/nextgenUp`  
**Date:** November 11, 2026  
**Scope:** Phase 4 Deliverables (Customer Frontend, Subscription Billing, Payment Integration, 8K Feasibility, Security, Production Readiness)  
**Audit Protocol:** Direct source code analysis, static security analysis, database migration review, frontend bundle inspection, runtime test suite execution. No code modifications performed during audit.

---

## EXECUTIVE VERDICT: CONDITIONALLY APPROVED

The Phase 4 architecture establishes a viable backend foundation for subscription billing, multi-tenancy, and credit accounting, with 108/108 pytest tests and 12/12 legacy unittest tests passing (120/120 total). However, **critical security vulnerabilities, severe frontend integration bugs, and unverified 8K/AI model marketing claims** prevent an unconditional approval.

Phase 5 (Production Hardening & Deployment Orchestration) **MUST NOT BEGIN** until the critical defects detailed below are remediated and verified.

---

## 1. 8K RESOLUTION & AI CAPABILITY AUDIT

### 1.1 8K Classification: **NOT IMPLEMENTED AND INFEASIBLE ON CURRENT CPU INFRASTRUCTURE**

| Inspection Area | Actual Implementation in Codebase | Audit Finding |
| :--- | :--- | :--- |
| **8K Neural Network Model** | None exists in `demerivo/ai/` or `static/models/`. | **Not Implemented.** |
| **8K Upscale Implementation** | `demerivo/ai/engine.py` lines 110–130 (`_upscale`). | Uses PIL Lanczos interpolation (`img.resize((target_w, target_h), Image.LANCZOS)`) followed by PIL `ImageEnhance.Sharpness(res).enhance(1.25)`. Clamps max dimension to 7680 (`max_dim = 7680`). Clamping Lanczos is **not AI super-resolution**. |
| **Plan-Specific Resolution Limits** | `demerivo/jobs/service.py:create_job` and `demerivo/api/routers/jobs.py:create_job`. | **Not Enforced.** Neither endpoint nor service inspects the tenant's current plan or the plan's `max_resolution_mp`. A Free plan user can submit an 8K request without restriction. |
| **Upload Dimension Verification** | `demerivo/security/upload_validator.py` lines 137–151. | Enforces a static global bounding box (`cfg.max_image_width = 8192`, `cfg.max_image_height = 8192`, `max_image_pixels = 33,554,432`). Does not adjust limits based on tenant subscription tier. |
| **Frontend Resolution Guards** | `frontend/src/components/PhotoStudio.tsx` lines 85–96. | Only checks file size (`20MB`) and extension. No dimension checking or plan resolution limit checks exist in the frontend studio. |
| **Memory Safety at 4K / 8K** | In `_upscale`, upscaling a 4096×4096 (16 MP) image by 4x without an explicit `is_8k` flag produces 16384×16384 pixels (~805 MB raw RGB, 3–5 GB RAM during float resampling). | **High OOM Risk.** Running unconstrained Lanczos or tiled inference on an Oracle Cloud Always Free VM (1 GB – 6 GB RAM) will crash the worker process. |
| **Real 4K / 8K Benchmarks** | `tests/test_ai_pipeline.py` lines 18–60. | **No High-Res Benchmarks Exist.** All unit and performance tests exclusively process synthetic **64×64 pixel thumbnails** upscaled to 128×128 or 256×256. |

### 1.2 Actual AI Engine Implementations vs. Catalog Claims
A severe discrepancy exists between the advertised models in `demerivo/ai/registry.py` and the actual inference code in `demerivo/ai/engine.py`:

| Operation | Model Claimed in `registry.py` | Actual Implementation in `engine.py` | Reality |
| :--- | :--- | :--- | :--- |
| `UPSCALE_2X` / `4X` | Real-ESRGAN (`realesr-general-x4v3.onnx`) | PIL Lanczos interpolation + Unsharp filter (`ImageEnhance.Sharpness`) | **Heuristic fallback**; ONNX model not loaded |
| `BG_REMOVE` | BiRefNet-lite (`birefnet-lite.onnx`) | Otsu thresholding + Sobel gradient edge matting (`cv2.threshold`, `cv2.Sobel`) | **Heuristic fallback**; ONNX model not loaded |
| `ENHANCE` | Real-ESRGAN + Downsample | OpenCV CLAHE on LAB luminance + PIL UnsharpMask | **Heuristic fallback**; no neural net |
| `DENOISE` | SCUNet (`scunet_color_real_psnr.onnx`) | `cv2.fastNlMeansDenoisingColored` | **OpenCV algorithm**; no neural net |
| `DEBLUR` | NAFNet-GoPro (`nafnet_gopro.onnx`) | Gaussian unsharp deconvolution (`cv2.addWeighted`) | **Heuristic filter**; no neural net |
| `COLORIZE` | DDColor-tiny (`ddcolor_tiny.onnx`) | Fixed sepia/warm chroma shift in LAB color space (`a + 8`, `b + 16`) | **Color shift filter**; no neural net |
| `INPAINT` | LaMa (`lama_fp32.onnx`) | OpenCV Telea inpainting (`cv2.inpaint`) with static center circle mask | **OpenCV algorithm**; no neural net |

**Recommendation:** Immediately remove all marketing claims referring to "8K Ultra-HD Upscaling", "SCUNet", "NAFNet", "BiRefNet", and "DDColor" until the ONNX Runtime integration from Phase 0 (`ai_engine.py` and `bg_remove.py`) is actually wired into `demerivo/ai/engine.py` and verified on CPU with real benchmarks.

---

## 2. RAZORPAY PAYMENT SECURITY AUDIT

### 2.1 Configuration and Secret Hygiene
- **Sandbox/Live Default:** `demerivo/config.py` lines 88–93 defaults to `payment_provider: Literal["mock", "razorpay", "stripe"] = "mock"`.
- **Secret Leakage:** Repository-wide audit (`git grep`) confirmed **zero live credentials** (`rzp_live_*`) are committed. All secrets are loaded from environment variables (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`).

### 2.2 Payment Gateway Verification Matrix

| Security Control | Code Location | Status | Technical Details |
| :--- | :--- | :---: | :--- |
| **Raw-Body Webhook Verification** | `demerivo/api/routers/billing.py:189-204` | **VERIFIED** | Accurately extracts `raw_body = await request.body()` and computes HMAC-SHA256 signature against `webhook_secret`. Constant-time comparison (`hmac.compare_digest`) prevents timing attacks. |
| **Client Checkout Signature Verification** | `demerivo/billing/razorpay_provider.py:73-95` | **VERIFIED** | Validates `HMAC-SHA256(order_id + "|" + payment_id, key_secret) == signature` before credit grant. |
| **Server-Side Pricing Validation** | `demerivo/billing/service.py:173-268` | **VERIFIED** | Prices, credits, and subscription durations are sourced strictly from server-side `PLANS_CATALOG` (`plans.py`). Client-supplied amounts are ignored. |
| **Cross-Tenant Payment Isolation** | `demerivo/api/routers/billing.py:72-135` | **VERIFIED** | Enforces `tenant_context` on checkout, verification, and history. Cross-tenant order injection is rejected. |
| **Role-Based Billing Restrictions (RBAC)** | `demerivo/api/routers/billing.py:82,112,146` | **VERIFIED** | Modifying subscriptions or verifying payments requires `owner` or `admin` tenant role. Regular `member` accounts receive HTTP 403 Forbidden. |
| **Live Order Creation Implementation** | `demerivo/billing/razorpay_provider.py:48-71` | **DEFECTIVE (STUB)** | `create_order_or_subscription` generates a local random string: `f"order_{uuid.uuid4().hex[:14]}"`. It **never calls the live Razorpay API** (`POST https://api.razorpay.com/v1/orders`). Live Razorpay checkout JS will fail immediately because the order ID does not exist on Razorpay's servers. |
| **Webhook Payment Table Idempotency** | `demerivo/billing/service.py:315-320` | **DEFECTIVE (DEAD CHECK)** | Webhook checks `PaymentModel.idempotency_key == f"webhook_{event_id}"`, but **never creates or saves a `PaymentModel`** record during renewal processing. Idempotency is only saved downstream by `credit_transactions` table constraint. |
| **Payment Failure & Refund States** | `demerivo/billing/service.py:321-360` | **INCOMPLETE** | Webhook processor only handles `payment.captured`, `subscription.charged`, `invoice.paid`, and `subscription.cancelled`. Events like `payment.failed`, `refund.processed`, or `subscription.paused` return unhandled without updating payment or subscription status. |

---

## 3. SUBSCRIPTION & CREDIT AUDIT

### 3.1 Plan & Credit Findings
1. **Initial Credit Allocation Discrepancy:**
   - In `demerivo/config.py:76`, `free_tier_initial_credits` is set to **`20`**.
   - In `demerivo/auth/dependencies.py:105-117`, new user personal workspace creation allocates **20 credits**.
   - In `demerivo/billing/plans.py:28-43`, the Free plan specifies **`monthly_credits = 5`**.
   - In `frontend/src/components/PricingTable.tsx:29`, marketing advertises **5 Monthly Credits**.
   - **Impact:** New users receive 20 credits instead of the advertised 5 credits upon registration.
2. **Single Credit Balance Pool (No Distinction of Credit Types):**
   - In `demerivo/db/models.py:254`, `WalletModel` only defines `balance: Mapped[int] = mapped_column(Integer, default=0)`.
   - Monthly subscription credits, top-up purchased credits, and bonus credits are merged into a single counter.
   - Monthly credit expiration is impossible without wiping out unexpired purchased top-up credits.
3. **No Subscription Expiration / Downgrade Enforcement:**
   - In `demerivo/billing/service.py:292`, `cancel_tenant_subscription(immediately=False)` sets `sub.cancel_at_period_end = True`.
   - **Audit Finding:** There is no background worker, cron task, or middleware in the entire repository that checks whether `current_period_end < now()` to transition canceled subscriptions to `free`. A canceled subscription remains `ACTIVE` indefinitely with its elevated tier.
4. **Concurrency & Atomic Balance Operations:**
   - In `demerivo/credits/wallet_service.py:126-146`, balance deduction is executed via atomic SQL:  
     `UPDATE wallets SET balance = balance - :amount WHERE tenant_id = :tenant_id AND balance >= :amount`.
   - Verified thread-safe and race-condition free under concurrent requests.

---

## 4. FRONTEND FUNCTIONAL & BUNDLE AUDIT

### 4.1 Production Bundle vs. React Source Discrepancy
- The React/TypeScript source files in `frontend/src/` (**23 files**) were **never compiled** into `frontend/dist/`.
- `frontend/node_modules/` **does not exist**; `npm install` and `npm run build` were never run.
- `frontend/dist/app.js` is a separate, manually written vanilla JavaScript application (1,398 lines) that attempts to replicate the React components, but contains multiple serious defects.

### 4.2 Verified Frontend Defects

| Component | Code Location | Defect Description | Severity |
| :--- | :--- | :--- | :---: |
| **Photo Studio & Image History** | `frontend/dist/app.js:1319`, `frontend/dist/app.js:675,686`, and `frontend/src/components/PhotoStudio.tsx:62`, `ImageHistory.tsx:102` | **Broken Image Downloads & Thumbnails (HTTP 422).** The frontend directly requests `/api/v1/storage/file/${output_key}` without query parameters. However, `demerivo/api/routers/storage_local.py:48-52` requires `expires: int = Query(...)` and `sig: str = Query(...)`. Every image preview and download fails with `422 Unprocessable Entity`. The frontend never calls the signed download endpoint `/api/v1/jobs/{job_id}/download`. | **CRITICAL** |
| **API Capabilities Loading** | `frontend/dist/app.js:77-81` | **Runtime TypeError in Initialization.** `app.js` executes `const capsRes = await apiRequest('/jobs/capabilities'); state.capabilities = capsRes.capabilities.filter(...)`. But `/api/v1/jobs/capabilities` returns a JSON Array `[...]`, not an object. `capsRes.capabilities` is `undefined`, throwing a `TypeError`. As a result, capabilities loading always fails, and the studio silently falls back to a hardcoded list with incorrect credit prices (`upscale_4x` shown as 3C instead of 4C). | **HIGH** |
| **Direct URL Refresh / Deep Links** | `demerivo/api/main.py:185-190` | **404 on Direct Navigation / Refresh.** FastAPI mounts `frontend/dist` using `StaticFiles(..., html=True)`. Deep URLs such as `/app/studio` or `/app/billing` return HTTP 404 because `StaticFiles` only resolves literal filenames and lacks an SPA fallback to `index.html`. | **MEDIUM** |
| **History & State Persistence** | `frontend/dist/app.js:26,1061` | **In-Memory Tab State Only.** Navigation tabs (`overview`, `studio`, `history`, `billing`, `wallet`, `settings`) do not use browser history (`history.pushState`) or URL hash fragments (`#/studio`). Refreshing `/app/` always kicks the user back to the overview dashboard. | **LOW** |

---

## 5. SECURITY AUDIT

### 5.1 Critical Security Vulnerabilities

#### DEFECT SEC-01: Unauthorized Credit Grant Endpoint (Arbitrary Free Credits)
- **Location:** `demerivo/api/routers/credits.py:98-133` (`grant_credits`)
- **Vulnerability:** The endpoint `POST /api/v1/credits/grant` accepts `{"amount": 10000, "description": "Free credits"}` and immediately allocates those credits to the active workspace wallet.
- **Flaw:** It depends only on `get_current_user` and `get_current_tenant`. It **does not check if the user is a superuser, administrator, or tenant owner**. Any regular member can invoke this endpoint to grant unlimited credits, completely bypassing subscription billing.
- **Classification:** **CRITICAL / CVSS 9.1 (Privilege Escalation & Billing Bypass)**.

#### DEFECT SEC-02: Missing Tenant Ownership Verification on Input Object Key
- **Location:** `demerivo/jobs/service.py:42-93` and `demerivo/api/routers/jobs.py:50-55`
- **Vulnerability:** When a user creates a processing job via `POST /api/v1/jobs`, the backend only verifies `storage.exists(request.input_key)`.
- **Flaw:** It fails to verify that `request.input_key.startswith(f"uploads/{tenant.id}/")`. An authenticated user in Tenant A who guesses or observes an object key belonging to Tenant B can submit Tenant B's image for processing, download the resulting output, and inspect private customer data.
- **Classification:** **HIGH / CVSS 7.7 (Cross-Tenant Insecure Direct Object Reference - IDOR)**.

#### DEFECT SEC-03: Stored Cross-Site Scripting (XSS) in Client Bundle
- **Location:** `frontend/dist/app.js:173, 480, 804`
- **Vulnerability:** `root.innerHTML` interpolates `state.user.display_name`, `state.user.email`, and error messages directly into DOM strings without HTML escaping:  
  `${state.user.display_name || 'Creator'}`
- **Flaw:** An attacker registering or updating display name with `<img src=x onerror=alert(document.cookie)>` will execute arbitrary JavaScript in the victim's session.
- **Classification:** **HIGH / CVSS 7.2 (Stored XSS)**.

#### DEFECT SEC-04: Completely Inactive Rate Limiting (Orphan Implementation)
- **Location:** `demerivo/security/rate_limiter.py:42-58`
- **Vulnerability:** The `rate_limit_dependency` function was created, but is **never imported or mounted in any router or middleware in `demerivo/api/`**.
- **Flaw:** The API has zero rate limiting active in production. Endpoints for password guessing (`/api/v1/auth/login`), direct upload flooding (`/api/v1/uploads/file`), and job queue saturation have no request throttling.
- **Classification:** **HIGH / CVSS 7.5 (Resource Exhaustion / Brute Force Risk)**.

---

## 6. PRODUCTION & INTEGRATION READINESS

| Component | Status | Production Risk |
| :--- | :---: | :--- |
| **FastAPI Backend Core** | **READY** | FastAPI 0.115+ application factory with structured lifespans, custom exception handlers, and clean router modularity. |
| **Database Models & Migrations** | **PARTIALLY READY** | SQLAlchemy models are complete. However, migrations are raw SQL files (`migrations/0002_*.sql`, `migrations/0003_*.sql`); no Alembic version tracking exists. |
| **Queue & Worker Architecture** | **READY** | Redis RPOPLPUSH queue with lease recovery and automatic fallback to `LocalDurableQueueProvider` if Redis is offline. |
| **Razorpay Provider** | **NOT READY** | Currently a stub generating offline mock order IDs; requires integration with the actual Razorpay Orders REST API before accepting real payments. |
| **Frontend Production Build** | **NOT READY** | Relies on a handwritten unescaped vanilla JS bundle (`app.js`) with broken image download links. React/TS source in `frontend/src/` must be built through Vite. |
| **SPA Catch-All Route** | **NOT READY** | Direct URL navigation to sub-routes returns 404. |

---

## 7. AUTOMATED TEST SUITE AUDIT

### 7.1 Re-Execution Verification (Executed November 11, 2026)
- **Pytest Suite:** `python -m pytest tests/ -v`  
  **Result:** **108 passed, 0 failed, 4 warnings in 9.59s (100% Pass Rate)**
- **Legacy Unittest Suite:** `python -m unittest tests.test_userfiles`  
  **Result:** **12 passed, 0 failed in 0.077s (100% Pass Rate)**
- **Total Tests Passed:** **120 tests**

### 7.2 Accurate Test Categorization

| Test Category | Test File(s) | Test Count | Scope & Limitations |
| :--- | :--- | :---: | :--- |
| **Unit Tests (Core Engines)** | `tests/test_ai_pipeline.py`, `tests/test_config.py`, `tests/test_credits.py`, `tests/test_userfiles.py` | 36 | Verified basic logic, pricing policy, and PIL/OpenCV execution. **Limitation:** AI pipeline tests exclusively run on 64×64 thumbnail images; no 4K/8K tests. |
| **Unit Tests (Security & Storage)** | `tests/test_upload_security.py`, `tests/test_storage.py` | 20 | Verified magic bytes, MIME types, path traversal blocks, and local storage. |
| **Multi-Tenancy & Auth Tests** | `tests/test_auth.py`, `tests/test_tenants.py`, `tests/test_job_authorization.py` | 20 | Verified JWT validation, workspace RBAC, and cross-tenant job querying. |
| **Job Lifecycle & Worker Tests** | `tests/test_jobs.py`, `tests/test_workers_and_queue.py` | 10 | Verified state machine transitions, leasing, and crash recovery. |
| **Payment Tests (Mock Provider Only)** | `tests/test_billing.py` | 10 | Verified HMAC checkout signatures, cancellations, and webhook idempotency. **Limitation:** Executed 100% in mock mode; zero live Razorpay API calls tested. |
| **API Integration Tests** | `tests/test_api.py` | 7 | Verified direct image upload and job lifecycle via direct router calls. |
| **Static Inspection Tests** | `tests/test_frontend.py` | 5 | Verified file existence and route mounting. **Limitation:** These are Python AST/file assertions, NOT browser or DOM tests. |
| **Browser / E2E Tests (Playwright / Cypress)** | *None exist in the repository.* | **0** | **Zero real browser or UI automation tests exist.** |
| **Load & Concurrency Tests** | *None exist in the repository.* | **0** | **Zero multi-client load or benchmark tests exist.** |

---

## 8. CODE LOCATIONS INSPECTED

```
demerivo/
  ai/
    base.py (lines 1-45: Exception definitions)
    engine.py (lines 45-131: UnifiedCPUAIEngine, _upscale Lanczos logic, clamping)
    registry.py (lines 23-117: OPERATIONS_CATALOG model claims)
  api/
    main.py (lines 38-67: lifespan, lines 181-192: static mounts and root redirect)
    routers/
      billing.py (lines 72-135: checkout and verification, lines 175-212: signed webhook)
      credits.py (lines 45-133: balance, transactions, and unauthenticated grant endpoint)
      jobs.py (lines 38-75: create_job, lines 146-181: get_job_download_url)
      storage_local.py (lines 48-75: local_presigned_download requiring expires & sig)
      uploads.py (lines 48-133: presigned and direct upload tenant scoping)
  auth/
    dependencies.py (lines 90-130: workspace auto-provisioning with 20 credits)
    jwt.py (lines 25-82: JWT creation and decode)
    provider.py (lines 62-120: Supabase offline auth store)
  billing/
    base.py (lines 1-50: PaymentProvider ABC)
    mock_provider.py (lines 1-85: SHA256 HMAC mock provider)
    plans.py (lines 27-92: PLANS_CATALOG definitions)
    razorpay_provider.py (lines 48-117: order generation and HMAC verification)
    service.py (lines 112-268: checkout and verification, lines 301-360: webhook handling)
  credits/
    pricing.py (lines 15-65: credit calculation, 8k multiplier)
    wallet_service.py (lines 54-165: atomic allocate and reserve queries)
  db/
    models.py (lines 53-64: OperationType, lines 237-275: WalletModel, lines 643-730: PaymentModel)
  security/
    rate_limiter.py (lines 13-58: unmounted SlidingWindowRateLimiter)
    upload_validator.py (lines 100-166: magic bytes and dimension limits)
  storage/
    local.py (lines 88-100: presigned URL HMAC signatures)
frontend/
  dist/
    app.js (lines 77-81: capabilities crash; line 1319: missing sig on resultUrl; lines 173, 480: XSS in innerHTML)
    styles.css (lines 1-200: CSS design tokens and touch-action)
  src/
    components/
      PhotoStudio.tsx (lines 62, 85-147: upload and resultUrl)
      ImageHistory.tsx (lines 101-106: unverified img src)
      PricingTable.tsx (lines 20-60: plan pricing display)
```

---

## 9. RECOMMENDED REMEDIATION PLAN (BEFORE PHASE 5)

*In accordance with audit rules, the following fixes are documented as required engineering recommendations without modifying codebase files during this audit.*

1. **Remediate Credit Grant Security Hole (`SEC-01`):**
   - In `demerivo/api/routers/credits.py`, delete the `POST /api/v1/credits/grant` endpoint or restrict it strictly to superusers using an environment-configured `ADMIN_API_KEY` header.
2. **Enforce Tenant Namespace on Input Keys (`SEC-02`):**
   - In `demerivo/api/routers/jobs.py:create_job`, add:
     ```python
     expected_prefix = f"uploads/{tenant.id}/"
     if not request.input_key.startswith(expected_prefix):
         raise HTTPException(status_code=403, detail="Access denied: input object belongs to another tenant.")
     ```
3. **Fix Frontend Image Preview & Download Links:**
   - In `frontend/dist/app.js` and `frontend/src/components/PhotoStudio.tsx` / `ImageHistory.tsx`, replace the hardcoded `/api/v1/storage/file/${output_key}` with an API call to `GET /api/v1/jobs/${job.id}/download`, which returns the authenticated presigned URL (`download_url`).
4. **Fix Capabilities Array Crash & Sanitize XSS in Frontend (`SEC-03`):**
   - In `frontend/dist/app.js:77`, check `Array.isArray(capsRes) ? capsRes : capsRes.capabilities || []`.
   - Implement an HTML escaping helper (`escapeHtml()`) for all user-supplied variables interpolated into `root.innerHTML`.
5. **Connect Rate Limiting (`SEC-04`):**
   - Mount `rate_limit_dependency` on sensitive endpoints (`/api/v1/auth/login`, `/api/v1/auth/register`, `/api/v1/uploads/file`, and `/api/v1/jobs`).
6. **Correct Free Plan Credit Discrepancy:**
   - Update `free_tier_initial_credits` in `demerivo/config.py` from 20 to 5 so new accounts align with the advertised 5 Free credits.
7. **Accurate Marketing & 8K Capability Alignment:**
   - Update `demerivo/billing/plans.py` and `frontend/src/components/` to remove references to 8K resolution until dedicated GPU nodes and a verified tiled AI pipeline are implemented.
8. **Implement Real Razorpay Orders API Call:**
   - In `demerivo/billing/razorpay_provider.py:create_order_or_subscription`, implement the actual HTTP call to `https://api.razorpay.com/v1/orders` using basic auth when `key_secret` is present.
9. **Add SPA Catch-All Fallback in FastAPI:**
   - In `demerivo/api/main.py`, add a fallback route returning `frontend/dist/index.html` for any unmatched path under `/app/` to support direct browser reloads.

---

## 10. FINAL AUDIT DECISION

**STATUS: CONDITIONALLY APPROVED**

- **Justification:** The multi-tenant backend architecture, database schemas, cryptographic signature validation, and core worker queue are well-designed and achieve 100% test pass rates across 120 automated tests.
- **Conditions for Phase 5 Approval:** Phase 5 (Production Hardening & Deployment Orchestration) cannot proceed until the **2 Critical defects (SEC-01 free credit exploit, broken image download URLs)** and **4 High-priority defects (cross-tenant IDOR, frontend XSS, capabilities crash, unmounted rate limiter)** are corrected and verified.

*Awaiting explicit user direction and authorization before proceeding.*
