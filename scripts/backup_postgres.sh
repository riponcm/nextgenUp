#!/usr/bin/env bash
# ==============================================================================
# Demerivo PixelFix — Hardened & Encrypted PostgreSQL Backup Script
# ==============================================================================
# Performs a compressed custom-format pg_dump, optional AES-256-CBC (PBKDF2)
# encryption, generates a SHA-256 checksum for audit and integrity, prunes
# backups older than the retention window, and provides configurable offsite
# cloud storage synchronization (S3/Cloudflare R2).
# ==============================================================================

set -euo pipefail

# Configuration with environment variable overrides
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-demerivo}"
POSTGRES_USER="${POSTGRES_USER:-demerivo}"
BACKUP_DIR="${BACKUP_DIR:-/backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

# Security: Encryption configuration (AES-256-CBC with PBKDF2)
# Keys must be provided via environment or secure key file, NEVER logged or committed to Git.
BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"
BACKUP_ENCRYPTION_KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-}"

# Offsite Cloud Storage (S3 / R2 compatible)
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-}"
BACKUP_S3_ENDPOINT_URL="${BACKUP_S3_ENDPOINT_URL:-}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-postgres/}"

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%SZ")
RAW_FILENAME="${POSTGRES_DB}_backup_${TIMESTAMP}.dump.gz"
RAW_BACKUP_PATH="${BACKUP_DIR}/${RAW_FILENAME}"

echo "=== [Demerivo PixelFix] Starting Database Backup ==="
echo "Timestamp:    ${TIMESTAMP}"
echo "Database:     ${POSTGRES_DB}"
echo "Host:         ${POSTGRES_HOST}:${POSTGRES_PORT}"
echo "User:         ${POSTGRES_USER}"
echo "Destination:  ${BACKUP_DIR}"

# Ensure backup directory exists with restricted permissions
mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

# Execute pg_dump directly piping to gzip
# -Fc uses PostgreSQL's custom format which is compressed and flexible for selective restores
echo "Executing pg_dump..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    -F c \
    -b -v | gzip -9 > "${RAW_BACKUP_PATH}"

# Verify file was created and is non-empty
if [ ! -s "${RAW_BACKUP_PATH}" ]; then
    echo "ERROR: Backup file ${RAW_BACKUP_PATH} is missing or empty!" >&2
    exit 1
fi

FINAL_BACKUP_PATH="${RAW_BACKUP_PATH}"

# -----------------------------------------------------------------------------
# Optional AES-256-CBC Encryption
# -----------------------------------------------------------------------------
if [ -n "${BACKUP_ENCRYPTION_KEY}" ] || [ -n "${BACKUP_ENCRYPTION_KEY_FILE}" ]; then
    echo "Encrypting backup archive with AES-256-CBC (PBKDF2)..."
    ENCRYPTED_PATH="${RAW_BACKUP_PATH}.enc"

    if [ -n "${BACKUP_ENCRYPTION_KEY_FILE}" ] && [ -f "${BACKUP_ENCRYPTION_KEY_FILE}" ]; then
        openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt \
            -in "${RAW_BACKUP_PATH}" \
            -out "${ENCRYPTED_PATH}" \
            -pass "file:${BACKUP_ENCRYPTION_KEY_FILE}"
    elif [ -n "${BACKUP_ENCRYPTION_KEY}" ]; then
        openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt \
            -in "${RAW_BACKUP_PATH}" \
            -out "${ENCRYPTED_PATH}" \
            -pass "env:BACKUP_ENCRYPTION_KEY"
    else
        echo "ERROR: Specified BACKUP_ENCRYPTION_KEY_FILE does not exist!" >&2
        exit 1
    fi

    # Securely remove unencrypted raw dump
    rm -f "${RAW_BACKUP_PATH}"
    FINAL_BACKUP_PATH="${ENCRYPTED_PATH}"
    echo "Encryption complete. Unencrypted archive removed."
else
    echo "Notice: BACKUP_ENCRYPTION_KEY not supplied. Storing compressed archive without secondary encryption."
fi

FILESIZE=$(stat -c%s "${FINAL_BACKUP_PATH}" 2>/dev/null || stat -f%z "${FINAL_BACKUP_PATH}" 2>/dev/null || wc -c < "${FINAL_BACKUP_PATH}")
echo "Backup archive created: $(basename "${FINAL_BACKUP_PATH}") (${FILESIZE} bytes)."

# -----------------------------------------------------------------------------
# SHA-256 Integrity Checksum
# -----------------------------------------------------------------------------
CHECKSUM_PATH="${FINAL_BACKUP_PATH}.sha256"
echo "Calculating SHA-256 checksum..."
sha256sum "${FINAL_BACKUP_PATH}" | awk '{print $1}' > "${CHECKSUM_PATH}"
echo "SHA-256 Checksum: $(cat "${CHECKSUM_PATH}")"

# -----------------------------------------------------------------------------
# Retention Pruning
# -----------------------------------------------------------------------------
echo "Pruning backups older than ${RETENTION_DAYS} days in ${BACKUP_DIR}..."
find "${BACKUP_DIR}" -type f -name "${POSTGRES_DB}_backup_*" -mtime "+${RETENTION_DAYS}" -exec rm -vf {} \;

# -----------------------------------------------------------------------------
# Configurable Offsite Cloud Synchronization (S3 / Cloudflare R2)
# -----------------------------------------------------------------------------
if [ -n "${BACKUP_S3_BUCKET}" ]; then
    echo "Initiating offsite cloud backup synchronization to s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}..."
    if command -v aws >/dev/null 2>&1; then
        AWS_CMD=("aws")
        if [ -n "${BACKUP_S3_ENDPOINT_URL}" ]; then
            AWS_CMD+=(--endpoint-url "${BACKUP_S3_ENDPOINT_URL}")
        fi
        "${AWS_CMD[@]}" s3 cp "${FINAL_BACKUP_PATH}" "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}$(basename "${FINAL_BACKUP_PATH}")"
        "${AWS_CMD[@]}" s3 cp "${CHECKSUM_PATH}" "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}$(basename "${CHECKSUM_PATH}")"
        echo "Offsite cloud synchronization completed successfully."
    else
        echo "WARNING: BACKUP_S3_BUCKET is configured, but 'aws' CLI is not found in PATH. Cloud sync skipped."
    fi
else
    echo "Notice: BACKUP_S3_BUCKET is not configured. Offsite cloud synchronization skipped."
fi

echo "=== [Demerivo PixelFix] Database Backup Completed Successfully ==="
