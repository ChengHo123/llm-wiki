#!/usr/bin/env bash
# 在 Oracle Cloud Ubuntu 24.04 ARM VM 上一次性執行的 bootstrap script。
#
# 用法（從你本機）：
#   scp scripts/bootstrap.sh ubuntu@<vm-ip>:/tmp/
#   ssh ubuntu@<vm-ip> "REPO_URL=https://github.com/<you>/llm-wiki.git bash /tmp/bootstrap.sh"
#
# 完成後會印出下一步該手動做什麼（建 .env、加 GitHub secrets、設 LINE webhook 等）。
set -euo pipefail

REPO_URL="${REPO_URL:?Need REPO_URL env var, e.g. https://github.com/you/llm-wiki.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/llm-wiki}"
SWAP_SIZE_GB="${SWAP_SIZE_GB:-2}"

echo "==> [1/6] apt update + 基本工具"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl ca-certificates git iptables-persistent

echo "==> [2/6] 安裝 Docker（官方 one-liner）"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "    ✓ Docker 已裝；本 session 還沒拿到 group 權限，後續 docker 指令我用 sudo 跑"
    DOCKER="sudo docker"
else
    echo "    ✓ Docker 已存在，跳過"
    DOCKER="docker"
fi

echo "==> [3/6] 開 80/443 firewall（OS 端 iptables）"
# 注意：Oracle VCN Security List 也要開（只能在 OCI Console 做，這個腳本沒辦法）
if ! sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
fi
if ! sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
fi
sudo netfilter-persistent save >/dev/null

echo "==> [4/6] 設定 ${SWAP_SIZE_GB}GB swap（避免 ingest 高峰 OOM）"
if ! swapon --show | grep -q '/swapfile'; then
    sudo fallocate -l "${SWAP_SIZE_GB}G" /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    fi
    echo "    ✓ swap 已啟用"
else
    echo "    ✓ swap 已存在，跳過"
fi

echo "==> [5/6] Clone repo 到 ${INSTALL_DIR}"
if [[ ! -d "${INSTALL_DIR}/.git" ]]; then
    sudo mkdir -p "${INSTALL_DIR}"
    sudo chown "$USER:$USER" "${INSTALL_DIR}"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
else
    echo "    ✓ Repo 已 clone，跳過"
fi
cd "${INSTALL_DIR}"
chmod +x scripts/*.sh

echo "==> [6/6] 產 deploy 用 SSH key 對（給 GitHub Actions）"
DEPLOY_KEY="${HOME}/.ssh/llm_wiki_deploy"
if [[ ! -f "${DEPLOY_KEY}" ]]; then
    mkdir -p "${HOME}/.ssh" && chmod 700 "${HOME}/.ssh"
    ssh-keygen -t ed25519 -N "" -C "llm-wiki-deploy" -f "${DEPLOY_KEY}"
    cat "${DEPLOY_KEY}.pub" >> "${HOME}/.ssh/authorized_keys"
    chmod 600 "${HOME}/.ssh/authorized_keys"
    echo "    ✓ Deploy key 已建立並加入 authorized_keys"
else
    echo "    ✓ Deploy key 已存在，跳過"
fi

cat <<EOF

==============================================================
✅  Bootstrap 完成。下面是「我做不了，你要自己手動做」的清單：
==============================================================

【1】Oracle VCN Security List 開 80/443
    Console → Networking → VCN → 你的 VCN → Default Security List
    → Add Ingress Rules：
        - 0.0.0.0/0 TCP 80
        - 0.0.0.0/0 TCP 443

【2】註冊 DuckDNS（免費 DDNS）
    https://www.duckdns.org → GitHub 登入 → 加 subdomain → 抄 token

【3】填 .env
    cd ${INSTALL_DIR}
    cp .env.example .env
    nano .env          # 填好所有必填欄位（見 DEPLOYMENT.md 第 5 步）

    用這條快速產隨機密碼：
        openssl rand -hex 24

    ⚠️ GHCR_OWNER 必須**全小寫**，例如 chengho123（不是 ChengHo123）

【4】GitHub repo Settings → Secrets and variables → Actions → 加：
    DEPLOY_HOST       = $(hostname -I | awk '{print $1}')   ← 但通常你要填 public IP
    DEPLOY_USER       = ${USER}
    DEPLOY_SSH_KEY    = ↓ 整段 private key 內容（含 BEGIN/END 行）↓

EOF
cat "${DEPLOY_KEY}"
cat <<EOF

【5】第一次 push tag 觸發部署：
    在你本機：
        git tag v0.1.0
        git push origin v0.1.0

    GitHub Actions 會：
      → buildx 跨平台打 arm64 image（首次 ~10 分鐘）
      → push 到 GHCR
      → SSH 進來這台 VM 跑 scripts/deploy.sh

【6】第一次部署成功後，把 GHCR package 設為 public（省 token 設定）：
    https://github.com/users/<you>/packages/container/llm-wiki-backend
        → Settings → Change visibility → Public
    對 llm-wiki-frontend 重複一次

【7】LINE Developers Console → Messaging API → Webhook URL 改成：
    https://<DUCKDNS_SUBDOMAIN>.duckdns.org/api/v1/linebot/webhook

【8】設每日備份 cron：
    sudo crontab -e
    # 加：
    0 3 * * * ${INSTALL_DIR}/scripts/backup-db.sh >> /var/log/llm-wiki-backup.log 2>&1

EOF
