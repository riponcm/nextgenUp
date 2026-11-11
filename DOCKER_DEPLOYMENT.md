# Demerivo PixelFix — Production Docker Deployment Guide

## 1. System Architecture Overview

Demerivo PixelFix employs a hardened, containerized multi-tier architecture where the public internet interfaces exclusively with a secure reverse proxy. Core application engines, task workers, and persistent data stores run in isolated internal networks with no public port exposure.

```
+-------------------------------------------------------------------------+
|                               INTERNET                                  |
+-------------------------------------------------------------------------+
                                    |
                           HTTPS (443) / HTTP (80)
                                    v
+-------------------------------------------------------------------------+
|  NGINX EDGE REVERSE PROXY (demerivo-pixelfix-nginx)                     |
|  - TLS 1.2 / TLS 1.3 Termination & Modern Ciphers                       |
|  - Rate Limiting Zones (auth: 10r/m, uploads: 30r/m, api: 60r/m)        |
|  - Administrative Network Isolation (/api/v1/admin/* CIDR allow/deny)   |
|  - Security Headers: HSTS, CSP, X-Frame-Options, X-Content-Type-Options |
+-------------------------------------------------------------------------+
               |                                            |
         frontend-net                                 frontend-net
               v                                            v
+-----------------------------+              +----------------------------+
|  FASTAPI APPLICATION SERVER |              |  REACT FRONTEND BUNDLE     |
|  (demerivo-pixelfix-api)    |              |  (Static distribution      |
|  - Multi-stage Docker build |              |   served via Nginx)        |
|  - Non-root UID 10001       |              +----------------------------+
|  - Health Probes (/healthz, |
|    /readyz)                 |
+-----------------------------+
               |
         backend-net (Internal isolated bridge, no external host exposure)
         +----------------------------------+-----------------------------+
         |                                  |                             |
         v                                  v                             v
+-----------------------+        +--------------------+        +--------------------+
|  BACKGROUND WORKER    |        |  POSTGRESQL 16     |        |  REDIS 7 CACHE &   |
|  (demerivo-worker)    |        |  (demerivo-postgres|        |  TASK BROKER       |
|  - Non-root UID 10001 |        |  - pg_data volume  |        |  (demerivo-redis)  |
|  - Crash recovery &   |        |  - Internal port   |        |  - redis_data vol  |
|    heartbeat telemetry|        |    5432 only       |        |  - Internal port   |
|  - PyTorch CPU runner |        +--------------------+        |    6379 only       |
+-----------------------+                                      +--------------------+
```

---

## 2. Security Boundaries & Port Segregation

1. **Host-Level Port Isolation:**
   - Only `nginx` maps ports to the host system (`80:80` and `443:443`).
   - `postgres`, `redis`, `api`, and `worker` publish **zero** host ports in `docker-compose.yml`.
   - All inter-service communication is confined to Docker internal networks (`frontend-net` and `backend-net`).
2. **Administrative Route Segregation (Finding H-2 Resolution):**
   - Direct web traffic to `/api/v1/admin/*` is filtered at the Nginx edge gateway via IP/CIDR rules.
   - Secondary application-layer defense-in-depth is enforced by `admin_network_isolation_middleware` in FastAPI.
3. **Least-Privilege Execution:**
   - Containers run under the unprivileged service user `demerivo` (UID 10001, GID 10001).
   - Root privileges, `setuid` binaries, and mutable host file system permissions are strictly blocked.

---

## 3. Production Deployment Procedure

### Step 1: Clone Repository and Prepare Environment File
```bash
cd /opt/demerivo-pixelfix
cp .env.example .env
chmod 600 .env
```

Configure `.env` with production secrets:
```ini
DEMERIVO_ENV=production
DEMERIVO_JWT_SECRET=<cryptographically-secure-string-at-least-32-chars>
DEMERIVO_ADMIN_SECRET=<cryptographically-secure-string-at-least-32-chars>
POSTGRES_PASSWORD=<strong-random-db-password>
DEMERIVO_DATABASE_URL=postgresql+asyncpg://demerivo:<strong-random-db-password>@postgres:5432/demerivo
REDIS_URL=redis://redis:6379/0
STORAGE_PROVIDER=s3
S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
S3_BUCKET_NAME=demerivo-pixelfix-prod
S3_ACCESS_KEY_ID=<your-access-key-id>
S3_SECRET_ACCESS_KEY=<your-secret-access-key>
PAYMENT_PROVIDER=razorpay
RAZORPAY_KEY_ID=<rzp_test_or_live_key>
RAZORPAY_KEY_SECRET=<rzp_secret>
RAZORPAY_WEBHOOK_SECRET=<rzp_webhook_secret>
DEMERIVO_ADMIN_ALLOWED_CIDR=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
```

### Step 2: TLS Certificate Provisioning
For production deployments, mount valid certificates from Let's Encrypt / Certbot:
```bash
mkdir -p nginx/certs
# Place your fullchain.pem and privkey.pem in nginx/certs/
chmod 600 nginx/certs/privkey.pem
chmod 644 nginx/certs/fullchain.pem
```

For staging or local integration validation, generate self-signed certificates:
```bash
bash nginx/certs/generate_self_signed.sh
```

### Step 3: Run Database Migrations
Before starting application traffic, apply SQL schema migrations to PostgreSQL:
```bash
# Start PostgreSQL database instance first
docker compose up -d postgres
sleep 5

# Apply migration scripts sequentially
docker compose exec -T postgres psql -U demerivo -d demerivo < migrations/0001_initial_schema.sql
docker compose exec -T postgres psql -U demerivo -d demerivo < migrations/0002_phase3_jobs.sql
docker compose exec -T postgres psql -U demerivo -d demerivo < migrations/0003_phase4_billing.sql
docker compose exec -T postgres psql -U demerivo -d demerivo < migrations/0004_phase5_phase6_admin_and_tokens.sql
```

### Step 4: Build and Launch Production Containers
```bash
docker compose build --no-cache
docker compose up -d
```

### Step 5: Verify Deployment Health
Check container state and healthcheck outputs:
```bash
# Verify all 5 containers are running and healthy
docker compose ps

# Test Liveness Probe (process responsiveness)
curl -k https://localhost/healthz

# Test Readiness Probe (database, queue, and storage health)
curl -k https://localhost/readyz
```

Expected readiness output:
```json
{
  "status": "ready",
  "environment": "production",
  "subsystems": {
    "database": "healthy",
    "queue": {
      "status": "healthy",
      "pending_jobs": 0
    },
    "storage": {
      "status": "healthy",
      "provider": "s3"
    }
  }
}
```

---

## 4. Local Development Deployment

To run the containerized stack locally with hot reloading and direct port access to PostgreSQL and Redis:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Exposed local endpoints in development mode:
- Web Application & API: `http://localhost:8000`
- PostgreSQL: `localhost:5432` (`demerivo_dev` / `demerivo_dev_password`)
- Redis: `localhost:6379`

---

## 5. Scaling Workers & Operational Commands

### Scaling Background AI Workers
To scale background inference workers horizontally based on CPU capacity:
```bash
docker compose up -d --scale worker=4
```

### Viewing Structured JSON Logs
```bash
# View aggregated container logs
docker compose logs -f

# Filter API logs for errors
docker compose logs -f api | grep '"level": "ERROR"'
```

### Performing Graceful Rolling Restarts
```bash
# Restarts API without dropping in-flight HTTP connections
docker compose restart api
```

### Zero-Downtime Worker Replacement
When workers receive `SIGTERM`, they finish in-flight inference tasks, record their final status, and terminate cleanly:
```bash
docker compose stop -t 60 worker
docker compose up -d worker
```
