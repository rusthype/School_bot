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

TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
DAY_OF_WEEK="$(date +%u)"   # 1-7 (Mon-Sun)
DAY_OF_MONTH="$(date +%d)"  # 01-31

DAILY_DIR="${BACKUP_DIR}/daily"
WEEKLY_DIR="${BACKUP_DIR}/weekly"
MONTHLY_DIR="${BACKUP_DIR}/monthly"

mkdir -p "${DAILY_DIR}" "${WEEKLY_DIR}" "${MONTHLY_DIR}" "${BACKUP_TMP}"

WORK_DIR="${BACKUP_TMP}/backup_${TIMESTAMP}"
mkdir -p "${WORK_DIR}"

cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

export PGPASSWORD="${DB_PASSWORD:-}"

DB_DUMP_FILE="${WORK_DIR}/db_${DB_NAME}_${TIMESTAMP}.sql"
pg_dump -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -f "${DB_DUMP_FILE}"

# Copy extra paths if provided
if [[ -n "${BACKUP_PATHS}" ]]; then
  IFS="," read -r -a PATHS <<< "${BACKUP_PATHS}"
  for p in "${PATHS[@]}"; do
    p_trimmed="${p//[[:space:]]/}"
    if [[ -n "${p_trimmed}" && -e "${p_trimmed}" ]]; then
      mkdir -p "${WORK_DIR}/files"
      cp -a "${p_trimmed}" "${WORK_DIR}/files/"
    fi
  done
fi

ARCHIVE_NAME="backup_${DB_NAME}_${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="${DAILY_DIR}/${ARCHIVE_NAME}"

tar -czf "${ARCHIVE_PATH}" -C "${WORK_DIR}" .

# Weekly backup on Sunday
if [[ "${DAY_OF_WEEK}" == "7" ]]; then
  cp -a "${ARCHIVE_PATH}" "${WEEKLY_DIR}/${ARCHIVE_NAME}"
fi

# Monthly backup on the 1st
if [[ "${DAY_OF_MONTH}" == "01" ]]; then
  cp -a "${ARCHIVE_PATH}" "${MONTHLY_DIR}/${ARCHIVE_NAME}"
fi

rotate_backups() {
  local dir="$1"
  local keep="$2"
  if [[ ! -d "${dir}" ]]; then
    return
  fi
  local count
  count=$(ls -1t "${dir}"/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
  if [[ "${count}" -le "${keep}" ]]; then
    return
  fi
  ls -1t "${dir}"/*.tar.gz | tail -n "$((count - keep))" | xargs -r rm -f
}

rotate_backups "${DAILY_DIR}" "${KEEP_DAILY}"
rotate_backups "${WEEKLY_DIR}" "${KEEP_WEEKLY}"
rotate_backups "${MONTHLY_DIR}" "${KEEP_MONTHLY}"

ARCHIVE_SIZE=$(du -h "${ARCHIVE_PATH}" | awk '{print $1}')
MSG="Backup completed. File: ${ARCHIVE_PATH} Size: ${ARCHIVE_SIZE}"

if [[ -n "${TELEGRAM_BOT_TOKEN}" && -n "${TELEGRAM_CHAT_ID}" ]]; then
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    -d "text=${MSG}" >/dev/null || true

  if [[ "${SEND_TELEGRAM_FILE}" == "true" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" \
      -F "chat_id=${TELEGRAM_CHAT_ID}" \
      -F "document=@${ARCHIVE_PATH}" >/dev/null || true
  fi
fi

echo "${MSG}"
