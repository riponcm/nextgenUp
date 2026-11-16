# DEMERIVO PIXELFIX — PHASE 1 IMPLEMENTATION REPORT
**Backend Foundation & Cloud Storage Abstraction**

- **Project:** Demerivo PixelFix
- **Version:** Phase 1 (Foundation)
- **Author:** Senior Backend Architect, AI/ML & DevOps Engineer
- **Base Repository:** NextGenUp (`https://github.com/riponcm/nextgenUp`)
- **Working Directory:** `D:\Projects\demerivo-pixelfix`
- **Date:** November 2026

---

## 1. Architecture Implemented

Phase 1 establishes a modular, decoupled SaaS backend foundation for Demerivo PixelFix without disrupting the existing NextGenUp desktop application or its self-hosted Flask API.

The SaaS foundation is isolated in the `demerivo/` top-level package and structured according to clean architecture principles:

```
demerivo-pixelfix/
├── demerivo/                     # Demerivo PixelFix SaaS Backend Package
│   ├── config.py                 # Pydantic Settings (Dev, Test, Prod)
│   ├── api/                      # FastAPI Application & Endpoints
│   │   ├── main.py               # App factory, CORS, exception handlers
│   │   └── routers/
│   │       ├── health.py         # System & Storage health checks
│   │       ├── uploads.py        # Presigned & Direct multipart uploads
│   │       ├── jobs.py           # Job lifecycle management & downloads
│   │       └── storage_local.py  # Local dev storage simulation endpoints
│   ├── storage/                  # Cloud Storage Abstraction Layer
│   │   ├── base.py               # Abstract StorageProvider interface
│   │   ├── local.py              # LocalStorage (dev/testing, traversal-safe)
│   │   ├── s3.py                 # S3CompatibleStorage (Cloudflare R2, SigV4)
│   │   └── factory.py            # Dependency injection storage provider
│   ├── security/                 # Image Security & Ingestion Validation
│   │   └── upload_validator.py   # Magic bytes, Pillow verify, bomb protection
│   ├── db/                       # Persistence Layer
│   │   ├── session.py            # SQLAlchemy 2.0 Engine & session management
│   │   └── models.py             # JobModel, JobStatus, OperationType
│   └── jobs/                     # Job Processing & Concurrency Control
│       ├── schemas.py            # Pydantic request/response schemas
│       ├── service.py            # Job lifecycle state machine & atomic DB ops
│       └── dispatcher.py         # Bounded concurrency dispatcher (CPU guard)
├── tests/                        # Automated Test Suites
│   ├── test_config.py            # Configuration & environment tests
│   ├── test_storage.py           # LocalStorage & S3CompatibleStorage tests
│   ├── test_upload_security.py   # Magic byte, decompression, & size tests
│   ├── test_jobs.py              # Job state machine & cancellation tests
│   ├── test_api.py               # FastAPI route handler & lifecycle tests
│   └── test_userfiles.py         # Original NextGenUp regression test suite
├── .env.example                  # Safe environment configuration template
├── DEMERIVO_PIXELFIX_PHASE_0_AUDIT.md # Phase 0 Audit deliverable
└── app.py, ai_engine.py, ...     # Original NextGenUp components (UNTOUCHED)
```

### Key Architectural Separation
1. **Desktop / Self-Hosted Compatibility:** The original Flask server (`app.py`), AI inference engines (`ai_engine.py`, `bg_remove.py`, `restore_engine.py`), and desktop UI (`desktop-ui/`, `src-tauri/`) remain completely untouched and operational.
2. **FastAPI SaaS Service:** A high-throughput, modern API interface handling image uploads, validation, job queues, and status polling.
3. **Storage Abstraction:** Direct compatibility with Cloudflare R2 via presigned URLs, keeping heavy image payloads off the API server while supporting local disk storage for development.
4. **Controlled Concurrency:** Bounded job dispatching prevents CPU thrashing during ONNX inference.

---

## 2. Files Created

| File | Purpose |
|------|---------|
| `.env.example` | Template for environment variables (dev, test, prod, R2 credentials, limits) |
| `demerivo/__init__.py` | Package initialization and version declaration (`0.1.0`) |
| `demerivo/config.py` | Pydantic `BaseSettings` supporting environment-driven configuration |
| `demerivo/storage/__init__.py` | Storage package exports |
| `demerivo/storage/base.py` | Abstract `StorageProvider` interface and custom storage exceptions |
| `demerivo/storage/local.py` | Secure local filesystem storage with path containment and HMAC signing |
| `demerivo/storage/s3.py` | S3/Cloudflare R2 compatible storage with pure-Python AWS SigV4 signer |
| `demerivo/storage/factory.py` | Dependency injection factory for storage providers |
| `demerivo/security/__init__.py` | Security package exports |
| `demerivo/security/upload_validator.py` | Magic bytes inspection, Pillow verification, decompression bomb guard, sanitization |
| `demerivo/db/__init__.py` | Database package exports |
| `demerivo/db/session.py` | SQLAlchemy 2.0 engine factory, scoped session manager, `init_db` |
| `demerivo/db/models.py` | `JobModel`, `JobStatus` enum, `OperationType` enum, composite indices |
| `demerivo/jobs/__init__.py` | Jobs package exports |
| `demerivo/jobs/schemas.py` | Pydantic models for job creation, status responses, and cancellation |
| `demerivo/jobs/service.py` | Atomic job management service and state transition enforcement |
| `demerivo/jobs/dispatcher.py` | Bounded concurrency worker queue (`asyncio.Queue`) |
| `demerivo/api/__init__.py` | API package exports |
| `demerivo/api/main.py` | FastAPI application factory, CORS, exception handlers, lifespan management |
| `demerivo/api/routers/__init__.py` | API router exports |
| `demerivo/api/routers/health.py` | Service liveness, system metadata, and storage connectivity endpoints |
| `demerivo/api/routers/uploads.py` | Presigned URL generation and direct multipart upload endpoints |
| `demerivo/api/routers/jobs.py` | Job submission, polling, cancellation, download, and deletion endpoints |
| `demerivo/api/routers/storage_local.py` | Local development storage bridge for PUT/GET operations |
| `tests/test_config.py` | Unit tests for configuration loading and environment overrides |
| `tests/test_storage.py` | Unit tests for LocalStorage and mocked S3CompatibleStorage |
| `tests/test_upload_security.py` | Security tests for magic bytes, size limits, dimensions, and path traversal |
| `tests/test_jobs.py` | Unit tests for job state transitions, atomic updates, and cancellation |
| `tests/test_api.py` | Integration tests for FastAPI endpoints, uploads, and job flows |

---

## 3. Files Modified

| File | Modification Details | Reason |
|------|----------------------|--------|
| `.gitignore` | Added `!.env.example` exception | Ensure the environment configuration template is tracked by Git while actual `.env` files remain strictly ignored. |

*Note: Zero existing source files from NextGenUp (`app.py`, `ai_engine.py`, `bg_remove.py`, `restore_engine.py`, `desktop-ui/`, etc.) were modified or deleted.*

---

## 4. Storage Architecture

Demerivo PixelFix implements an abstract storage interface via `StorageProvider`:

```python
class StorageProvider(ABC):
    def upload_file(self, file_data: bytes, key: str, content_type: str = "application/octet-stream") -> str: ...
    def download_file(self, key: str) -> bytes: ...
    def delete_file(self, key: str) -> bool: ...
    def exists(self, key: str) -> bool: ...
    def get_file_size(self, key: str) -> int: ...
    def get_presigned_upload_url(self, key: str, content_type: str = "application/octet-stream", expires_in: int = 900) -> str: ...
    def get_presigned_download_url(self, key: str, expires_in: int = 900) -> str: ...
```

### Storage Implementations
1. **`LocalStorage` (Local / Development / CI):**
   - Stores files in a sandboxed directory (e.g. `storage_data/`).
   - Strict containment verification: rejects path traversals (`..`, absolute paths, drive letters, null bytes) using `os.path.commonpath`.
   - Presigned URLs are generated using HMAC-SHA256 signatures with timestamps, routing through `/api/v1/storage/local/download/{key}` and `/upload/{key}`.
2. **`S3CompatibleStorage` (Cloudflare R2 / AWS S3):**
   - Directly compatible with Cloudflare R2 endpoints (`https://<account-id>.r2.cloudflarestorage.com`).
   - Implements native **AWS Signature Version 4 (SigV4)** query-string signing in pure Python (standard library `hmac`, `hashlib`, `urllib`).
   - Eliminates heavy external binary dependencies while enabling browser clients to upload and download directly to/from Cloudflare R2 without passing image data through the API server.
   - Buckets remain private by default; presigned URLs have configurable TTL (default: 900 seconds / 15 minutes).
3. **Dependency Injection Factory (`get_storage`):**
   - Automatically injects the configured storage backend based on `STORAGE_BACKEND=local|s3`.

---

## 5. Database Schema

The persistence layer is built on **SQLAlchemy 2.0** with strict typing and PostgreSQL compatibility (SQLite used in local dev and automated tests).

### `jobs` Table Definition
```sql
CREATE TABLE jobs (
    id VARCHAR(36) PRIMARY KEY,              -- UUID4 string
    status VARCHAR(16) NOT NULL,             -- pending, queued, processing, completed, failed, cancelled
    operation VARCHAR(32) NOT NULL,          -- upscale, bg_remove, denoise, deblur, enhance
    input_key VARCHAR(512) NOT NULL,         -- Storage object key (e.g. uploads/uuid.png)
    output_key VARCHAR(512),                 -- Storage object key (e.g. outputs/uuid.png)
    progress INTEGER NOT NULL DEFAULT 0,     -- 0 to 100 percentage
    parameters TEXT,                         -- JSON string of execution arguments
    error_message TEXT,                      -- Error trace or message on failure
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Performance & Queue Indices
CREATE INDEX ix_jobs_status ON jobs (status);
CREATE INDEX ix_jobs_operation ON jobs (operation);
CREATE INDEX ix_jobs_created_at ON jobs (created_at);
CREATE INDEX ix_jobs_status_created_at ON jobs (status, created_at);
CREATE INDEX ix_jobs_operation_status ON jobs (operation, status);
```

### Lifecycle State Machine
`JobService` strictly enforces valid state transitions to guarantee consistency:
- `pending` -> `queued` | `cancelled`
- `queued` -> `processing` | `cancelled`
- `processing` -> `completed` | `failed` | `cancelled`
- `completed` -> (Terminal)
- `failed` -> (Terminal)
- `cancelled` -> (Terminal)

Illegal transitions (e.g. `completed` -> `processing`) raise an `InvalidJobStateTransition` exception and are rejected by the API.

---

## 6. API Endpoint List

All endpoints are documented via FastAPI OpenAPI interactive documentation (`/docs` and `/redoc`).

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| `GET` | `/health` | Basic service liveness check | No |
| `GET` | `/api/v1/health` | Comprehensive API status, version, and environment metadata | No |
| `GET` | `/api/v1/storage/health` | Verifies storage provider connectivity and accessibility | No |
| `POST` | `/api/v1/uploads/presigned` | Generates a presigned PUT URL for direct client-to-R2 upload | Phase 2 (JWT) |
| `POST` | `/api/v1/uploads/file` | Direct multipart image upload with multi-layer security validation | Phase 2 (JWT) |
| `POST` | `/api/v1/jobs` | Enqueues a new image enhancement job | Phase 2 (JWT) |
| `GET` | `/api/v1/jobs/{job_id}` | Polls job status, progress percentage, error message, and metadata | Phase 2 (JWT) |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Cancels a pending or queued job | Phase 2 (JWT) |
| `GET` | `/api/v1/jobs/{job_id}/download` | Returns a secure, time-limited presigned download URL for the result | Phase 2 (JWT) |
| `DELETE` | `/api/v1/jobs/{job_id}` | Deletes job record and associated storage assets | Phase 2 (JWT) |
| `PUT` | `/api/v1/storage/local/upload/{key}` | Local development PUT upload simulator | Internal Dev |
| `GET` | `/api/v1/storage/local/download/{key}`| Local development GET download simulator | Internal Dev |

---

## 7. Security Measures

1. **Never Trust Client Metadata:**
   - Client-reported MIME types and original filenames are discarded for storage keys and validation.
2. **True Magic Bytes Verification:**
   - Inspects raw binary header bytes:
     * JPEG: `\xFF\xD8\xFF`
     * PNG: `\x89PNG\r\n\x1a\n`
     * WebP: `RIFF` ... `WEBP`
     * BMP: `BM`
     * TIFF: `II*\x00` (little-endian) or `MM\x00*` (big-endian)
   - Executable, script, or polyglot files disguised as images (e.g. `payload.php.jpg`) are rejected immediately.
3. **Pillow Header Verification:**
   - Runs `Image.open().verify()` to confirm structural integrity without decoding pixel data into memory.
4. **Decompression Bomb Protection:**
   - Enforces `Image.MAX_IMAGE_PIXELS = 50_000_000` (50 Megapixels).
   - Rejects malicious small files designed to expand into gigabytes of RAM during decompression.
5. **Strict File Size & Dimension Constraints:**
   - Maximum upload size: 25 MB (configurable via `MAX_UPLOAD_SIZE_BYTES`).
   - Minimum dimensions: 10 x 10 pixels.
   - Maximum dimensions: 8192 x 8192 pixels.
6. **Path Traversal & Filename Sanitization:**
   - Filenames are sanitized using regex (`[^a-zA-Z0-9_.-]`), path separators (`/`, `\\`) and `..` tokens are stripped.
   - Files are stored under cryptographically random collision-free keys:
     `uploads/{uuid4}_{timestamp}.{ext}`
7. **Time-Limited Presigned URLs:**
   - Cloud storage buckets remain 100% private.
   - Presigned download URLs expire after 900 seconds (15 minutes).
8. **Controlled CPU Concurrency:**
   - `JobDispatcher` limits concurrent workers (`MAX_CONCURRENT_WORKERS = 2` on CPU-first instances) using an bounded queue to prevent out-of-memory errors and CPU starvation.

---

## 8. Dependencies Added or Changed

No disruptive external dependencies were added to the existing workspace. The implementation relies on standard library features and robust existing packages:

- `fastapi` & `pydantic` / `pydantic-settings`: API framework and configuration.
- `sqlalchemy` (2.0): ORM and database persistence.
- `pillow`: Image inspection and decompression protection.
- `httpx`: Storage client and API test harness.
- `pytest`: Automated testing framework.
- **Pure-Python AWS SigV4 Signer:** Created in `demerivo/storage/s3.py` using `hmac`, `hashlib`, and `urllib`. Avoids `boto3` installation blockers in isolated environments.

Existing `requirements.txt` remains intact and unchanged.

---

## 9. Automated Test Results

All test suites were executed on Python 3.14.6 (Windows x86_64).

### Test Suite Execution Output
```
============================= test session starts =============================
platform win32 -- Python 3.14.6, pytest-8.4.2, pluggy-1.6.0 -- python.exe
cachedir: .pytest_cache
rootdir: D:\Projects\demerivo-pixelfix
plugins: anyio-4.14.2
collecting ... collected 34 items

tests/test_config.py::TestConfiguration::test_default_configuration PASSED [  2%]
tests/test_config.py::TestConfiguration::test_environment_override PASSED [  5%]
tests/test_storage.py::TestLocalStorage::test_delete_file PASSED         [  8%]
tests/test_storage.py::TestLocalStorage::test_file_not_found PASSED      [ 11%]
tests/test_storage.py::TestLocalStorage::test_path_traversal_prevention PASSED [ 14%]
tests/test_storage.py::TestLocalStorage::test_presigned_urls PASSED      [ 17%]
tests/test_storage.py::TestLocalStorage::test_upload_and_download PASSED [ 20%]
tests/test_storage.py::TestS3CompatibleStorage::test_mocked_exists_and_delete PASSED [ 23%]
tests/test_storage.py::TestS3CompatibleStorage::test_mocked_upload_and_download PASSED [ 26%]
tests/test_storage.py::TestS3CompatibleStorage::test_path_traversal_prevention PASSED [ 29%]
tests/test_storage.py::TestS3CompatibleStorage::test_presigned_download_url_generation PASSED [ 32%]
tests/test_storage.py::TestS3CompatibleStorage::test_presigned_upload_url_generation PASSED [ 35%]
tests/test_upload_security.py::TestUploadSecurity::test_corrupted_image_header_rejected PASSED [ 38%]
tests/test_upload_security.py::TestUploadSecurity::test_dimension_limits_enforced PASSED [ 41%]
tests/test_upload_security.py::TestUploadSecurity::test_empty_file_rejected PASSED [ 44%]
tests/test_upload_security.py::TestUploadSecurity::test_filename_sanitization_and_traversal PASSED [ 47%]
tests/test_upload_security.py::TestUploadSecurity::test_oversized_file_rejected PASSED [ 50%]
tests/test_upload_security.py::TestUploadSecurity::test_spoofed_extension_rejected PASSED [ 52%]
tests/test_upload_security.py::TestUploadSecurity::test_unsupported_magic_signature_rejected PASSED [ 55%]
tests/test_upload_security.py::TestUploadSecurity::test_valid_jpeg_upload PASSED [ 58%]
tests/test_upload_security.py::TestUploadSecurity::test_valid_png_upload PASSED [ 61%]
tests/test_upload_security.py::TestUploadSecurity::test_valid_webp_upload PASSED [ 64%]
tests/test_jobs.py::TestJobLifecycle::test_invalid_lifecycle_transition_raises PASSED [ 67%]
tests/test_jobs.py::TestJobLifecycle::test_job_cancellation PASSED       [ 70%]
tests/test_jobs.py::TestJobLifecycle::test_job_creation PASSED           [ 73%]
tests/test_jobs.py::TestJobLifecycle::test_job_deletion PASSED           [ 76%]
tests/test_jobs.py::TestJobLifecycle::test_job_not_found PASSED          [ 79%]
tests/test_jobs.py::TestJobLifecycle::test_valid_lifecycle_transitions PASSED [ 82%]
tests/test_api.py::TestAPIEndpoints::test_cancel_job_endpoint PASSED     [ 85%]
tests/test_api.py::TestAPIEndpoints::test_direct_image_upload_and_job_lifecycle PASSED [ 88%]
tests/test_api.py::TestAPIEndpoints::test_direct_upload_invalid_payload PASSED [ 91%]
tests/test_api.py::TestAPIEndpoints::test_health_endpoints PASSED        [ 94%]
tests/test_api.py::TestAPIEndpoints::test_job_not_found PASSED           [ 97%]
tests/test_api.py::TestAPIEndpoints::test_presigned_upload_endpoint PASSED [100%]

============================= 34 passed in 4.59s ==============================
```

### NextGenUp Legacy Regression Suite
```
python -m unittest tests.test_userfiles
............
----------------------------------------------------------------------
Ran 12 tests in 0.180s

OK
```

**Total Tests:** 46 tests executed, 46 passed, 0 failures, 0 regressions.

---

## 10. Known Limitations

1. **In-Memory Worker Dispatcher:**
   - In Phase 1, the `JobDispatcher` uses an in-process `asyncio.Queue` for local job dispatching. For multi-server production deployment, Phase 3 will introduce Redis-backed Celery or RQ workers.
2. **Single-Tenant Job Scope:**
   - Jobs are not yet scoped to a `user_id` because user authentication is strictly designated for Phase 2.
3. **Inference Pipeline Decoupled:**
   - The Phase 1 job dispatcher simulates job completion for API contract validation; binding to the actual ONNX inference engines is planned for Phase 3.
4. **GFPGAN Commercial Exclusion:**
   - GFPGAN remains explicitly excluded from commercial workflows due to non-commercial upstream dataset licensing.

---

## 11. Deployment Requirements

### Runtime Environment
- **Operating System:** Linux (Ubuntu 22.04 LTS / Debian 12 / Oracle Linux) or Windows 10/11.
- **Python Version:** Python 3.10 to 3.14.
- **CPU:** Minimum 2 cores (Oracle Cloud Always Free Ampere ARM64 4-core recommended).
- **RAM:** Minimum 2 GB (4 GB+ recommended for ONNX model loading).

### Environment Variables
Configure the following in `.env` (derived from `.env.example`):
```env
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=<secure-random-64-character-string>
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/demerivo_pixelfix
STORAGE_BACKEND=s3
R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<cloudflare-access-key-id>
R2_SECRET_ACCESS_KEY=<cloudflare-secret-access-key>
R2_BUCKET_NAME=demerivo-pixelfix-production
```

### Running the API Service
```bash
uvicorn demerivo.api.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 4
```

---

## 12. Phase 2 Recommendations

With the backend foundation, storage abstraction, and upload security verified, Phase 2 should focus on:

1. **Customer Authentication & Multi-Tenancy:**
   - JWT-based authentication with refresh tokens (FastAPI OAuth2 password bearer).
   - User entity model (`users` table) with email verification and password hashing (Argon2 / bcrypt).
   - Associate all jobs and uploaded storage keys with `user_id`.
2. **Credit Accounting & Subscription Foundation:**
   - Ledger model for user credits (`credit_transactions` table).
   - Tier definitions: Free (limited daily credits), Starter, Pro, Studio.
   - Atomic credit deduction check before job creation.
3. **Database Migrations with Alembic:**
   - Introduce Alembic migration scripts for managed schema evolution on production PostgreSQL.
4. **Client SDK & Webhook Preparation:**
   - Webhook structures for asynchronous job completion notifications.
   - Frontend API client contracts matching OpenAPI specifications.

---

**Report Status:** Complete & Verified  
**Tests:** 34/34 Phase 1 pytest passed, 12/12 legacy NextGenUp tests passed  
**Next Step:** Awaiting user review and approval to proceed with Phase 2.
