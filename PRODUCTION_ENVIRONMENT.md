# Demerivo PixelFix — Production Environment Specification & Secrets Guide

## 1. Overview & Validation Framework

Demerivo PixelFix enforces automated startup validation via `validate_production_configuration()` (`demerivo/config.py`). Whenever `DEMERIVO_ENV=production` is declared, the backend performs fail-safe sanity checks before serving any HTTP traffic or initiating queue polling.

### Automated Production Guardrails:
1. **JWT Secret Enforcement:** Rejects blank values, strings shorter than 32 characters, and development defaults (`demerivo-pixelfix-default-jwt-secret-for-dev-32chars`).
2. **Platform Admin Secret Enforcement:** Rejects tokens with fewer than 32 characters.
3. **Database Guard:** Blocks execution if SQLite connection strings (`sqlite:///...`) are supplied; requires a production PostgreSQL URI (`postgresql+asyncpg://...`).
4. **Mock Payment Prohibition:** Blocks `PAYMENT_PROVIDER=mock`; requires Razorpay configuration.
5. **Local Storage Prohibition:** Blocks `STORAGE_PROVIDER=local`; requires S3/Cloudflare R2 object storage.

---

## 2. Complete Environment Variable Reference

| Variable | Type | Required in Prod | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `DEMERIVO_ENV` | `string` | **Yes** | `development` | Deployment environment: `development`, `testing`, `production`. |
| `DEMERIVO_DEBUG` | `boolean` | No | `false` | Enables verbose stack traces. **Must be false in production.** |
| `DEMERIVO_LOG_FORMAT` | `string` | No | `json` | Logging format: `json` (structured JSON with redaction) or `text`. |
| `DEMERIVO_LOG_LEVEL` | `string` | No | `INFO` | Logging threshold: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `DEMERIVO_JWT_SECRET` | `string` | **Yes** | — | Cryptographically random secret for signing and validating auth tokens (>= 32 chars). |
| `DEMERIVO_ADMIN_SECRET` | `string` | **Yes** | — | High-entropy secret token for internal administrative APIs (>= 32 chars). |
| `DEMERIVO_ADMIN_EMAILS` | `string` | **Yes** | — | Comma-separated list of platform admin emails authorized for bootstrap elevation. |
| `DEMERIVO_DATABASE_URL` | `string` | **Yes** | — | PostgreSQL async connection URI: `postgresql+asyncpg://user:pass@host:5432/dbname`. |
| `REDIS_URL` | `string` | **Yes** | `redis://redis:6379/0` | Redis instance URL used for task queues and distributed rate limiting. |
| `STORAGE_PROVIDER` | `string` | **Yes** | `s3` | Object storage engine. Must be `s3` (Cloudflare R2 / AWS S3) in production. |
| `S3_ENDPOINT_URL` | `string` | **Yes** | — | Custom S3 endpoint URL (e.g. `https://<account_id>.r2.cloudflarestorage.com`). |
| `S3_BUCKET_NAME` | `string` | **Yes** | — | Target object bucket name for raw uploads and enhanced outputs. |
| `S3_ACCESS_KEY_ID` | `string` | **Yes** | — | Access Key ID with scoped Read/Write permissions on the bucket. |
| `S3_SECRET_ACCESS_KEY` | `string` | **Yes** | — | Secret Access Key for object storage API authentication. |
| `PAYMENT_PROVIDER` | `string` | **Yes** | `razorpay` | Gateway engine: `razorpay` (or `stripe`). Mock payments are rejected in prod. |
| `RAZORPAY_KEY_ID` | `string` | **Yes** | — | Razorpay API Key ID (e.g., `rzp_test_...` or `rzp_live_...`). |
| `RAZORPAY_KEY_SECRET` | `string` | **Yes** | — | Razorpay API Secret Key. |
| `RAZORPAY_WEBHOOK_SECRET` | `string` | **Yes** | — | Webhook verification secret configured in Razorpay merchant dashboard. |
| `EMAIL_PROVIDER` | `string` | **Yes** | `smtp` | Email backend: `smtp`, `console`, `mock`, or `none`. |
| `SMTP_HOST` | `string` | Conditional | — | Outbound SMTP relay hostname (e.g., `smtp.sendgrid.net`, `smtp.mailgun.org`). |
| `SMTP_PORT` | `integer` | Conditional | `587` | Outbound SMTP relay port (typically `587` for STARTTLS). |
| `SMTP_USER` | `string` | Conditional | — | SMTP service account username. |
| `SMTP_PASSWORD` | `string` | Conditional | — | SMTP service account password or API token. |
| `SMTP_USE_TLS` | `boolean` | No | `true` | Enforces STARTTLS during SMTP transport. |
| `EMAIL_FROM_ADDRESS` | `string` | No | `noreply@demerivo.com` | Authenticated sender address in verification emails. |
| `DEMERIVO_ADMIN_ALLOWED_CIDR`| `string` | No | `127.0.0.1/32,10.0.0.0/8...`| Subnets permitted to access `/api/v1/admin/*` routes. |
| `DEMERIVO_ADMIN_ENFORCE_NETWORK_ISOLATION`| `boolean` | No | `true` | When true, rejects administrative API calls originating from non-whitelisted IPs. |

---

## 3. Secret Generation & Rotation Procedures

### Generating Cryptographic Secrets
Use system CSPRNG (`openssl` or Python `secrets`) to generate high-entropy strings:
```bash
# Generate 32-byte (64-character hex) JWT Secret
openssl rand -hex 32

# Generate 32-byte (64-character hex) Admin Secret Token
openssl rand -hex 32

# Generate PostgreSQL Master Password
openssl rand -base64 24
```

### Zero-Downtime Secret Rotation

#### 1. Rotating JWT Secret
1. Update JWT signature verification to accept both `OLD_JWT_SECRET` and `NEW_JWT_SECRET` during the transition window (24 hours).
2. Issue new tokens using `NEW_JWT_SECRET`.
3. After 24 hours, deprecate `OLD_JWT_SECRET` completely.

#### 2. Rotating Administrative Secret (`DEMERIVO_ADMIN_SECRET`)
1. Generate the replacement secret.
2. Update the `.env` file on backend instances and internal administrative tooling simultaneously.
3. Restart API instances (`docker compose restart api`).

#### 3. Rotating Payment Webhook Secrets
1. In the Razorpay Dashboard, generate a new Webhook Secret under **Settings -> Webhooks**.
2. Add the secondary secret to `.env`.
3. Restart the API instances to verify signatures against the updated secret.
