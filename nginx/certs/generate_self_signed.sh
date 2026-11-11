#!/usr/bin/env bash
# Generate self-signed TLS certificates for local Nginx testing
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="${SCRIPT_DIR}"

mkdir -p "${CERTS_DIR}"

if [[ ! -f "${CERTS_DIR}/fullchain.pem" || ! -f "${CERTS_DIR}/privkey.pem" ]]; then
    echo "Generating self-signed TLS certificates for development/testing..."
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "${CERTS_DIR}/privkey.pem" \
        -out "${CERTS_DIR}/fullchain.pem" \
        -subj "/C=US/ST=State/L=City/O=Demerivo/CN=localhost"
    echo "Certificates generated successfully in ${CERTS_DIR}"
else
    echo "Certificates already exist in ${CERTS_DIR}."
fi
