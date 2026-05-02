#!/usr/bin/env bash
# 快速連上 Oracle VM
# 用法：
#   ./scripts/ssh-oracle.sh           # 直接進入 shell
#   ./scripts/ssh-oracle.sh 'cmd'     # 執行單一指令後離開

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${ORACLE_HOST:-llm-wiki-chk.duckdns.org}"
USER="${ORACLE_USER:-ubuntu}"

# 依序找 SSH key：環境變數 > .gitignore 裡的 key > 預設 ~/.ssh/id_*
find_key() {
    if [[ -n "${ORACLE_KEY:-}" ]]; then
        echo "${ORACLE_KEY}"
        return
    fi
    local key
    key=$(find "${ROOT_DIR}" -maxdepth 1 -name "ssh-key-*.key" ! -name "*.pub" 2>/dev/null | sort -r | head -1)
    if [[ -n "${key}" ]]; then
        echo "${key}"
        return
    fi
    echo ""
}

KEY=$(find_key)

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${KEY}" ]]; then
    SSH_OPTS+=(-i "${KEY}")
fi

echo ">>> SSH → ${USER}@${HOST}"
[[ -n "${KEY}" ]] && echo "    key: ${KEY}"

if [[ $# -gt 0 ]]; then
    exec ssh "${SSH_OPTS[@]}" "${USER}@${HOST}" "$@"
else
    exec ssh "${SSH_OPTS[@]}" "${USER}@${HOST}"
fi
