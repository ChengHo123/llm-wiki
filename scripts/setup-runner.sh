#!/usr/bin/env bash
# 在 Oracle VM 上安裝 GitHub Actions self-hosted runner。
# 執行前先去 GitHub repo → Settings → Actions → Runners → New self-hosted runner
# 取得 RUNNER_TOKEN（有效 1 小時），再執行：
#   REPO=your-github-user/llm-wiki RUNNER_TOKEN=xxxx bash scripts/setup-runner.sh
set -euo pipefail

: "${REPO:?請設定 REPO，例如：export REPO=ChengHo123/llm-wiki}"
: "${RUNNER_TOKEN:?請設定 RUNNER_TOKEN（從 GitHub Settings → Actions → Runners 取得）}"

RUNNER_VERSION="2.317.0"
RUNNER_DIR="${HOME}/actions-runner"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

echo ">>> Downloading runner ${RUNNER_VERSION} (arm64)…"
curl -sSfLO "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz"
tar xzf "actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz"
rm  "actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz"

echo ">>> Configuring runner…"
./config.sh \
    --url "https://github.com/${REPO}" \
    --token "${RUNNER_TOKEN}" \
    --name "oracle-arm64" \
    --labels "self-hosted,linux,arm64" \
    --work "_work" \
    --unattended \
    --replace

echo ">>> Installing as systemd service…"
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status

echo ""
echo "Done. Runner 'oracle-arm64' is now active."
echo "Verify at: https://github.com/${REPO}/settings/actions/runners"
