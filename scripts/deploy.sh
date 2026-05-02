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

# 重啟過程中正在處理的文件會在啟動時由 _requeue_pending 自動 requeue，
# 並由 run_ingest 以「成功才刪 stale 頁」清掉上次跑一半的殘留。不需要等 queue 清空。

if [[ "$NO_PULL" == "false" ]]; then
    docker compose -f docker-compose.prod.yml pull backend frontend
fi
docker compose -f docker-compose.prod.yml up -d

# Caddyfile 是 volume mount，container spec 沒變時 `up -d` 不會重啟 caddy。
# 顯式 reload 讓 Caddyfile 改動每次部署都生效（首次啟動會失敗，忽略）。
docker compose -f docker-compose.prod.yml exec -T caddy \
    caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || true

docker image prune -f

echo ">>> Status:"
docker compose -f docker-compose.prod.yml ps
