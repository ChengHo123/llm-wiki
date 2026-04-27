#!/usr/bin/env bash
# Daily Postgres backup. Dumps both wiki and litellm DBs, keeps last 7 days.
# Install via cron:
#   sudo crontab -e
#   0 3 * * * /opt/llm-wiki/scripts/backup-db.sh >> /var/log/llm-wiki-backup.log 2>&1
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/llm-wiki}"
KEEP_DAYS="${KEEP_DAYS:-7}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "${BACKUP_DIR}"
cd "${PROJECT_DIR}"

# Load POSTGRES_USER from .env
set -a
# shellcheck disable=SC1091
source .env
set +a

TS="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/llm-wiki_${TS}.sql.gz"

echo "[$(date -Is)] backing up to ${DUMP_FILE}"

# pg_dumpall captures both wiki + litellm DBs in one shot
docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_dumpall -U "${POSTGRES_USER}" --clean --if-exists \
    | gzip > "${DUMP_FILE}"

# Rotate
find "${BACKUP_DIR}" -name 'llm-wiki_*.sql.gz' -mtime +"${KEEP_DAYS}" -delete

echo "[$(date -Is)] done. retained backups:"
ls -lh "${BACKUP_DIR}" | tail -n +2
