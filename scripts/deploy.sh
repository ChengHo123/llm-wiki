#!/usr/bin/env bash
# Run on the Oracle VM to roll out a new image tag.
# Invoked by GitHub Actions deploy workflow, or manually:
#   ./scripts/deploy.sh v1.0.0
set -euo pipefail

TAG="${1:-latest}"
NO_PULL=false
for arg in "$@"; do
    [[ "$arg" == "--no-pull" ]] && NO_PULL=true
done
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "ERROR: /opt/llm-wiki/.env not found. Copy .env.example and configure first." >&2
    exit 1
fi

# Persist IMAGE_TAG so subsequent `docker compose` calls (without args) stay on the deployed version.
if grep -q '^IMAGE_TAG=' .env; then
    sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=${TAG}|" .env
else
    echo "IMAGE_TAG=${TAG}" >> .env
fi

# Load .env so GHCR_TOKEN / GHCR_USERNAME are available below.
set -a
# shellcheck disable=SC1091
source .env
set +a

# Login to GHCR only if package is private (token provided in .env).
# Public packages need no auth — leave GHCR_TOKEN unset.
if [[ -n "${GHCR_TOKEN:-}" ]]; then
    echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USERNAME:-${GHCR_OWNER}}" --password-stdin
fi

echo ">>> Deploying tag: ${TAG}"

# 等待 ingest queue 清空，避免重啟中斷進行中的文件處理
DRAIN_TIMEOUT=1800  # 最多等 30 分鐘
DRAIN_ELAPSED=0
echo ">>> Waiting for ingest queue to drain…"
while true; do
    QUEUE=$(docker compose -f docker-compose.prod.yml exec -T postgres \
        psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -c \
        "SELECT COUNT(*) FROM documents WHERE status IN ('queued', 'processing');" \
        2>/dev/null | tr -d ' \n')
    if [[ "${QUEUE}" == "0" ]]; then
        echo ">>> Queue empty — proceeding."
        break
    fi
    if [[ ${DRAIN_ELAPSED} -ge ${DRAIN_TIMEOUT} ]]; then
        echo "WARNING: Queue still has ${QUEUE} item(s) after ${DRAIN_TIMEOUT}s — deploying anyway." >&2
        break
    fi
    echo "    Queue depth: ${QUEUE} — waiting 15s… (${DRAIN_ELAPSED}s elapsed)"
    sleep 15
    DRAIN_ELAPSED=$((DRAIN_ELAPSED + 15))
done

if [[ "$NO_PULL" == "false" ]]; then
    docker compose -f docker-compose.prod.yml pull backend frontend
fi
docker compose -f docker-compose.prod.yml up -d
docker image prune -f

echo ">>> Status:"
docker compose -f docker-compose.prod.yml ps
