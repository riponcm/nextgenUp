# Demerivo PixelFix — PostgreSQL Database Backup & Disaster Recovery Guide

## 1. Objectives & Backup Strategy

| Metric | Target | Description |
| :--- | :--- | :--- |
| **RPO (Recovery Point Objective)** | **<= 1 hour** | Maximum acceptable data loss window during catastrophic infrastructure failure. |
| **RTO (Recovery Time Objective)** | **<= 15 minutes** | Maximum acceptable time to provision replacement database and restore operational state. |
| **Retention Window** | **30 Days** | Daily backups retained locally and pruned automatically. |
| **Encryption Standard** | **AES-256-CBC** | Authenticated PBKDF2 encryption (100,000 iterations) with external secret key. |
| **Integrity Assurance** | **SHA-256** | Every backup file is accompanied by an authenticated cryptographic digest. |

---

## 2. Backup Execution (`scripts/backup_postgres.sh`)

The automated backup script performs a streaming compressed PostgreSQL dump using custom format (`-Fc`), applies optional AES-256-CBC encryption, computes a SHA-256 checksum, prunes archives older than the retention threshold, and synchronizes to offsite cloud object storage (S3 / Cloudflare R2).

### Manual Execution with AES-256 Encryption
```bash
# Run backup inside container or on database host
export POSTGRES_HOST=postgres
export POSTGRES_PORT=5432
export POSTGRES_DB=demerivo
export POSTGRES_USER=demerivo
export POSTGRES_PASSWORD=<strong-db-password>
export BACKUP_DIR=/backups/postgres
export RETENTION_DAYS=30

# Encryption Key (Must be kept outside Git and outside the backup archive)
export BACKUP_ENCRYPTION_KEY="<strong-random-32-byte-hex-or-passphrase>"
# OR point to a securely protected key file:
# export BACKUP_ENCRYPTION_KEY_FILE=/etc/demerivo/backup_master.key

# Optional Offsite S3 Cloud Sync
export BACKUP_S3_BUCKET="demerivo-pixelfix-db-backups"
export BACKUP_S3_ENDPOINT_URL="https://<account_id>.r2.cloudflarestorage.com"
export BACKUP_S3_PREFIX="postgres/"

bash scripts/backup_postgres.sh
```

### Encryption Key Management & Rotation
1. **Key Generation:** Generate a cryptographically secure key:
   ```bash
   openssl rand -hex 32 > /etc/demerivo/backup_master.key
   chmod 400 /etc/demerivo/backup_master.key
   ```
2. **Key Storage:** Store the key in a corporate secret manager (e.g. AWS Secrets Manager, HashiCorp Vault, 1Password Secrets). NEVER store the key in Git or in the backup directory itself.
3. **Key Rotation:** When rotating the backup encryption key:
   - Generate `backup_master_v2.key`.
   - Update backup cron/jobs to use the new key for subsequent dumps.
   - Retain previous keys in the secret manager labeled by date range to ensure historical backups remain decryptable throughout their 30-day retention window.

### Script Execution Log Output:
```
=== [Demerivo PixelFix] Starting Database Backup ===
Timestamp:    20261111_195500Z
Database:     demerivo
Host:         postgres:5432
User:         demerivo
Destination:  /backups/postgres/demerivo_backup_20261111_195500Z.dump.gz
Executing pg_dump...
Backup created successfully. Size: 1459203 bytes.
Calculating SHA-256 checksum...
SHA-256 Checksum: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
Pruning backups older than 30 days in /backups/postgres...
=== [Demerivo PixelFix] Database Backup Completed Successfully ===
```

### Automated Nightly Cron Job
To schedule daily backups at 02:00 UTC and upload to encrypted offsite cloud storage:

```bash
# Open root or service crontab
crontab -e

# Add automated nightly backup and remote sync
0 2 * * * /opt/demerivo-pixelfix/scripts/backup_postgres.sh >> /var/log/demerivo_backup.log 2>&1
30 2 * * * aws s3 sync /backups/postgres s3://demerivo-pixelfix-db-backups/ --delete
```

---

## 3. Database Restoration Procedure (`scripts/restore_postgres.sh`)

### Safety Controls
1. **Cryptographic Checksum Verification:** Before touching the target database, `restore_postgres.sh` compares the archive against its companion `.sha256` checksum file to guarantee data integrity.
2. **Explicit User Confirmation:** Unless `--force` is provided, the script pauses and requires typing `RESTORE-<dbname>` to prevent accidental overwrites.
3. **Connection Termination:** Disconnects all active client sessions (`pg_terminate_backend`) before restoration begins.

### Restoration Command
```bash
# Syntax: bash scripts/restore_postgres.sh <path-to-backup.dump.gz> [--force]
bash scripts/restore_postgres.sh /backups/postgres/demerivo_backup_20261111_195500Z.dump.gz
```

### Interactive Restoration Workflow:
```
=== [Demerivo PixelFix] Database Restore Initiation ===
Target DB:    demerivo
Target Host:  postgres:5432
User:         demerivo
Backup File:  /backups/postgres/demerivo_backup_20261111_195500Z.dump.gz
Verifying SHA-256 integrity...
Integrity verified: SHA-256 matches.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
WARNING: This operation will OVERWRITE existing data in database 'demerivo'.
Ensure you have a recent backup of the current database state before continuing.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Type 'RESTORE-demerivo' to confirm database restoration: RESTORE-demerivo
Terminating active connections to 'demerivo'...
Restoring database from compressed archive...
=== [Demerivo PixelFix] Database Restore Completed Successfully ===
```

---

## 4. Post-Restoration Verification Checklist

After restoring the database, run these verification queries to confirm table integrity and row counts:

```sql
-- Connect to restored database
psql -U demerivo -d demerivo

-- Verify table existence
\dt

-- Check critical table counts
SELECT 'users' AS table_name, count(*) FROM users
UNION ALL
SELECT 'tenants', count(*) FROM tenants
UNION ALL
SELECT 'wallets', count(*) FROM wallets
UNION ALL
SELECT 'subscriptions', count(*) FROM subscriptions
UNION ALL
SELECT 'jobs', count(*) FROM jobs
UNION ALL
SELECT 'email_verification_tokens', count(*) FROM email_verification_tokens;

-- Verify recent active subscriptions
SELECT id, tenant_id, plan_code, status, current_period_end 
FROM subscriptions 
WHERE status = 'active' 
ORDER BY current_period_end DESC LIMIT 5;
```

---

## 5. Disaster Recovery Runbook

### Scenario A: Unintended Table Drop or Data Corruption
1. Immediately stop API and worker containers to prevent further conflicting writes:
   ```bash
   docker compose stop api worker
   ```
2. Locate the most recent valid backup file in `/backups/postgres/` or pull from remote S3.
3. Execute `restore_postgres.sh` with the verified backup archive.
4. Run post-restoration verification SQL checks.
5. Restart API and worker containers:
   ```bash
   docker compose start api worker
   ```
6. Probe `/readyz` endpoint to confirm full system readiness.

### Scenario B: Complete Host / Hardware Loss
1. Provision a new server instance running Ubuntu 22.04 / 24.04 LTS.
2. Install Docker 24+ and Docker Compose v2.
3. Clone repository and restore `.env` from secure secret manager (e.g. AWS Secrets Manager, 1Password).
4. Pull the latest verified database backup from cloud backup bucket.
5. Start PostgreSQL container:
   ```bash
   docker compose up -d postgres
   ```
6. Execute `restore_postgres.sh <backup_file> --force`.
7. Start remaining containers (`docker compose up -d`).
8. Point DNS records to the new host IP.
