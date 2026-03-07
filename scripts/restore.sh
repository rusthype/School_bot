#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${SCRIPT_DIR}/backup.conf"

if [[ ! -f "${CONF_FILE}" ]]; then
  echo "Missing config: ${CONF_FILE}" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${CONF_FILE}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/backup.tar.gz" >&2
  echo "Available backups:" >&2
  find "${BACKUP_DIR}" -type f -name "*.tar.gz" -print | sort -r | head -n 20 >&2
  exit 1
fi

ARCHIVE="$1"
if [[ ! -f "${ARCHIVE}" ]]; then
  echo "Backup not found: ${ARCHIVE}" >&2
  exit 1
fi

RESTORE_DIR="${BACKUP_TMP}/restore_$(date +"%Y%m%d_%H%M%S")"
mkdir -p "${RESTORE_DIR}"

cleanup() {
  rm -rf "${RESTORE_DIR}"
}
trap cleanup EXIT

tar -xzf "${ARCHIVE}" -C "${RESTORE_DIR}"

DB_DUMP_FILE=$(find "${RESTORE_DIR}" -type f -name "db_${DB_NAME}_*.sql" | head -n 1)
if [[ -z "${DB_DUMP_FILE}" ]]; then
  echo "Database dump not found in archive." >&2
  exit 1
fi

export PGPASSWORD="${DB_PASSWORD:-}"
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -f "${DB_DUMP_FILE}"

if [[ -d "${RESTORE_DIR}/files" ]]; then
  echo "Restored files are available at: ${RESTORE_DIR}/files"
fi

echo "Restore completed."
