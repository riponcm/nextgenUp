#!/usr/bin/env bash
# ==============================================================================
# Demerivo PixelFix — Hardened & Decrypting PostgreSQL Restore Script
# ==============================================================================
# Restores compressed or AES-256-CBC encrypted PostgreSQL dumps created by
# backup_postgres.sh.
# Requires explicit confirmation before overwriting the active database.
# ==============================================================================

set -euo pipefail

BACKUP_FILE="${1:-${BACKUP_FILE:-}}"
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-demerivo}"
POSTGRES_USER="${POSTGRES_USER:-demerivo}"
FORCE="${2:-${CONFIRM_RESTORE:-false}}"

BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"
BACKUP_ENCRYPTION_KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-}"

if [ -z "${BACKUP_FILE}" ]; then
    echo "Usage: $0 <path-to-backup-file> [--force]" >&2
    echo "Supported formats: .dump.gz or .dump.gz.enc" >&2
    exit 1
fi

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "ERROR: Backup file ${BACKUP_FILE} does not exist!" >&2
    exit 1
fi

echo "=== [Demerivo PixelFix] Database Restore Initiation ==="
echo "Target DB:    ${POSTGRES_DB}"
echo "Target Host:  ${POSTGRES_HOST}:${POSTGRES_PORT}"
echo "User:         ${POSTGRES_USER}"
echo "Backup File:  ${BACKUP_FILE}"

# -----------------------------------------------------------------------------
# Integrity check: verify checksum if companion file exists
# -----------------------------------------------------------------------------
CHECKSUM_FILE="${BACKUP_FILE}.sha256"
if [ -f "${CHECKSUM_FILE}" ]; then
    echo "Verifying SHA-256 integrity..."
    EXPECTED_HASH=$(cat "${CHECKSUM_FILE}" | awk '{print $1}')
    ACTUAL_HASH=$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')
    if [ "${EXPECTED_HASH}" != "${ACTUAL_HASH}" ]; then
        echo "FATAL: Checksum verification failed!" >&2
        echo "Expected: ${EXPECTED_HASH}" >&2
        echo "Actual:   ${ACTUAL_HASH}" >&2
        exit 1
    fi
    echo "Integrity verified: SHA-256 matches."
else
    echo "WARNING: No companion .sha256 file found. Proceeding without checksum verification."
fi

# -----------------------------------------------------------------------------
# Decryption (if encrypted archive .enc is provided)
# -----------------------------------------------------------------------------
RESTORE_SOURCE="${BACKUP_FILE}"
CLEANUP_DECRYPTED=false

if [[ "${BACKUP_FILE}" == *.enc ]]; then
    echo "Encrypted archive detected. Preparing decryption..."
    if [ -z "${BACKUP_ENCRYPTION_KEY}" ] && [ -z "${BACKUP_ENCRYPTION_KEY_FILE}" ]; then
        echo "ERROR: Decryption key required for .enc file. Set BACKUP_ENCRYPTION_KEY or BACKUP_ENCRYPTION_KEY_FILE." >&2
        exit 1
    fi

    DECRYPTED_TMP=$(mktemp /tmp/demerivo_restore_XXXXXX.dump.gz)
    chmod 600 "${DECRYPTED_TMP}"
    CLEANUP_DECRYPTED=true

    # Clean up decrypted file on exit or failure
    trap 'rm -f "${DECRYPTED_TMP}"' EXIT INT TERM

    echo "Decrypting archive with AES-256-CBC (PBKDF2)..."
    if [ -n "${BACKUP_ENCRYPTION_KEY_FILE}" ] && [ -f "${BACKUP_ENCRYPTION_KEY_FILE}" ]; then
        openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -salt \
            -in "${BACKUP_FILE}" \
            -out "${DECRYPTED_TMP}" \
            -pass "file:${BACKUP_ENCRYPTION_KEY_FILE}"
    else
        openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -salt \
            -in "${BACKUP_FILE}" \
            -out "${DECRYPTED_TMP}" \
            -pass "env:BACKUP_ENCRYPTION_KEY"
    fi

    RESTORE_SOURCE="${DECRYPTED_TMP}"
    echo "Decryption successful."
fi

# -----------------------------------------------------------------------------
# Confirmation prompt unless forced
# -----------------------------------------------------------------------------
if [ "${FORCE}" != "--force" ] && [ "${FORCE}" != "true" ]; then
    echo ""
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "WARNING: This operation will OVERWRITE existing data in database '${POSTGRES_DB}'."
    echo "Ensure you have a recent backup of the current database state before continuing."
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo ""
    read -r -p "Type 'RESTORE-${POSTGRES_DB}' to confirm database restoration: " CONFIRMATION
    if [ "${CONFIRMATION}" != "RESTORE-${POSTGRES_DB}" ]; then
        echo "Aborted by user. No changes made."
        exit 0
    fi
fi

echo "Terminating active connections to '${POSTGRES_DB}'..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();" || true

echo "Restoring database from compressed archive..."
gunzip -c "${RESTORE_SOURCE}" | PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_restore \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    --clean \
    --if-exists \
    --no-owner \
    --no-acl \
    -v || {
        STATUS=$?
        # pg_restore exit code 1 represents warnings which are often non-fatal in Postgres
        if [ ${STATUS} -eq 1 ]; then
            echo "pg_restore finished with warnings (code 1)."
        else
            echo "ERROR: pg_restore failed with exit code ${STATUS}!" >&2
            exit ${STATUS}
        fi
    }

echo "=== [Demerivo PixelFix] Database Restore Completed Successfully ==="
