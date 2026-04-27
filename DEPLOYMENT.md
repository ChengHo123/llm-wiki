# Oracle Cloud 部署指南

把整套服務部上 Oracle Cloud Free Tier ARM 機器，CI/CD 由 git tag 觸發。

## 架構

```
GitHub repo
    │ git tag vX.Y.Z + push
    ▼
GitHub Actions (.github/workflows/deploy.yml)
    ├── docker buildx build linux/arm64
    ├── push → ghcr.io/<you>/llm-wiki-{backend,frontend}:vX.Y.Z
    └── ssh → Oracle VM → scripts/deploy.sh
                            │
                            ▼
                Oracle ARM A1 (Ubuntu)
                ├── postgres        (內網，不對外)
                ├── litellm         (內網，不對外)
                ├── backend         (內網，pull image)
                ├── frontend        (nginx，內網，pull image)
                ├── duckdns         (同步 public IP 至 *.duckdns.org)
                └── caddy           (port 80/443，自動 Let's Encrypt HTTPS)
                        │
                        ▼
                  你的 LINE bot / 瀏覽器
```

---

## 一次性 Bootstrap

### 1. 開 Oracle Cloud Free Tier ARM VM

1. 註冊 https://cloud.oracle.com（要綁信用卡驗證身分，不會扣款）
2. 建立 Instance：
   - **Shape**: Ampere A1 Flex
   - **OCPU / RAM**: 4 OCPU / 24 GB（一次吃滿，反正都免費）
   - **Image**: Canonical Ubuntu 22.04 或 24.04
   - **Boot volume**: 100 GB 以上（最多 200 GB 免費）
   - **SSH key**: 上傳你的 public key（產生：`ssh-keygen -t ed25519`）
3. 抄下 public IP

> Tip：熱門 region 常常 ARM 缺貨。撞到「out of capacity」就換 region 或寫腳本重試開機。

### 2. 開放 80/443 port

**(a) Oracle VCN Security List**
Console → Networking → Virtual Cloud Networks → 你的 VCN → Default Security List → Add Ingress Rules：
| Source CIDR | Protocol | Dest Port |
|---|---|---|
| 0.0.0.0/0 | TCP | 80 |
| 0.0.0.0/0 | TCP | 443 |

**(b) VM 上的 iptables（Ubuntu 預設會擋）**
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

### 3. 裝 Docker

```bash
ssh ubuntu@<your-vm-ip>

# Docker 官方一行裝
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit  # 重新 ssh 讓 group 生效
```

### 4. 拉 repo 到 /opt/llm-wiki

```bash
ssh ubuntu@<your-vm-ip>
sudo mkdir -p /opt/llm-wiki && sudo chown ubuntu:ubuntu /opt/llm-wiki
git clone https://github.com/<you>/llm-wiki.git /opt/llm-wiki
cd /opt/llm-wiki
chmod +x scripts/*.sh
```

### 5. 設 .env

```bash
cp .env.example .env
nano .env   # 填好所有 production 區段的值
```

必填欄位（其餘 dev 用）：

| 欄位 | 說明 |
|---|---|
| `POSTGRES_PASSWORD` | 隨機強密碼（用 `openssl rand -hex 24`） |
| `LITELLM_MASTER_KEY` | 隨機 sk- 開頭字串 |
| `LITELLM_UI_PASSWORD` | 隨機 |
| `LITELLM_DATABASE_URL` | 對應 POSTGRES_PASSWORD：`postgresql://wiki:<上面密碼>@postgres:5432/litellm` |
| `LLM_API_KEY` | 第一次 boot 後用 ssh tunnel 進 LiteLLM UI 建 virtual key 後填回再 redeploy（見下方「進 LiteLLM UI」） |
| `LLM_MODEL` | 對應 `litellm/config.yaml` 設定的模型名 |
| `GHCR_OWNER` | 你的 GitHub username 全小寫 |
| `DUCKDNS_SUBDOMAIN` / `DUCKDNS_TOKEN` | https://www.duckdns.org 註冊取得 |
| `DOMAIN` | `<DUCKDNS_SUBDOMAIN>.duckdns.org` |
| `LE_EMAIL` | 你的 email |
| `LINE_*` | LINE Developers Console（晚點設） |

> ⚠️ `.env` 已在 `.gitignore`，**永遠不要 commit**。所有 secret 只存在 VM 上。

### 6. 把 GHCR package 設為 public（推薦）

第一次 push image 後（見下方第一次部署），到：
- https://github.com/users/<you>/packages/container/llm-wiki-backend → Settings → Change visibility → Public
- 同樣對 `llm-wiki-frontend` 做一次

這樣 Oracle VM pull image 時不用任何 token。如果你要保持 private，就在 `.env` 補 `GHCR_USERNAME` 和 `GHCR_TOKEN`（read:packages scope 的 PAT）。

### 7. 設 GitHub Actions secrets

Repo → Settings → Secrets and variables → Actions → New repository secret：

| Secret | 值 |
|---|---|
| `DEPLOY_HOST` | Oracle VM 的 public IP |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | 整段 private key 內容（`cat ~/.ssh/id_ed25519`） |
| `DEPLOY_PORT` | （可選）非預設 22 才填 |

> 建議為部署單獨產一把 deploy key：`ssh-keygen -t ed25519 -f deploy_key -N ""`，
> 把 `deploy_key.pub` `cat` 後 append 到 VM 的 `~/.ssh/authorized_keys`，private key 給 GitHub。

### 8. 第一次部署

兩種方式：

**(a) 手動先打一次 image，避免 deploy script 找不到 image**
```bash
# 在你本機（會慢，因為 buildx arm 跨平台）
docker buildx create --use
docker buildx build --platform linux/arm64 \
    -t ghcr.io/<you>/llm-wiki-backend:latest \
    --push ./backend
docker buildx build --platform linux/arm64 \
    -t ghcr.io/<you>/llm-wiki-frontend:latest \
    --push ./frontend
```
然後在 VM 上：
```bash
cd /opt/llm-wiki
./scripts/deploy.sh latest
```

**(b) 推薦：直接打第一個 tag 觸發 GitHub Actions**
```bash
git tag v0.1.0
git push origin v0.1.0
```
Actions 會自動 build → push → ssh deploy。第一次 build arm64 大約 8~12 分鐘（之後有 cache 會快很多）。

### 9. 設定 LINE webhook URL

- 部署成功後到 https://your-subdomain.duckdns.org 確認頁面正常
- LINE Developers Console → Messaging API → Webhook URL：
  `https://your-subdomain.duckdns.org/api/v1/linebot/webhook`
- 點 "Verify"

### 10. 設每日備份 cron

```bash
sudo crontab -e
# 加入這行：每天凌晨 3 點備份
0 3 * * * /opt/llm-wiki/scripts/backup-db.sh >> /var/log/llm-wiki-backup.log 2>&1
```

備份存在 `/var/backups/llm-wiki/`，預設保留 7 天。**強烈建議**之後再加一段把 dump 同步到 Cloudflare R2 / Backblaze B2（免費 10 GB），避免 Oracle 砍帳號時資料一起消失。

---

## 日常使用

### 部署新版本

```bash
git tag v1.2.3
git push origin v1.2.3
```

GitHub Actions 自動 build + 部署，全程約 5~10 分鐘（有 cache 時）。

### 重新部署現有版本（不重 build）

Actions UI → Deploy workflow → Run workflow → 填現有 tag（或留空表示用當前 tag）。

### 查看狀態

```bash
ssh ubuntu@<vm-ip>
cd /opt/llm-wiki
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend
```

### Rollback

```bash
# 在 VM 上
cd /opt/llm-wiki
git checkout v1.2.2
./scripts/deploy.sh v1.2.2
```

或從 Actions UI 用舊 tag 觸發。

### 進 DB

prod 沒裝 pgadmin。要進 DB 用 ssh tunnel：
```bash
ssh -L 5432:localhost:5432 ubuntu@<vm-ip>
# 然後本機用 pgadmin/psql 連 localhost:5432
```
或直接：
```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U wiki
```

### 進 LiteLLM UI

LiteLLM admin UI 不對外（避免暴露 admin 面板）。用 ssh tunnel：
```bash
ssh -L 4000:localhost:4000 ubuntu@<vm-ip>
# 但容器內 port 4000 只對 docker network 開放，需要從 VM 上轉發：
ssh ubuntu@<vm-ip>
docker compose -f docker-compose.prod.yml exec -it litellm sh
# 或直接從 VM curl：curl http://localhost:4000  ← 不通，因為沒 bind 主機 port
```

最簡單的做法：暫時 expose litellm port 給 ssh tunnel 用：
```bash
ssh ubuntu@<vm-ip>
cd /opt/llm-wiki
# 臨時加 port mapping
docker compose -f docker-compose.prod.yml run --rm --service-ports -p 4000:4000 litellm &
# 另開終端 ssh tunnel
```

更省事的選擇：在 `docker-compose.prod.yml` 的 litellm 加上 `ports: ["127.0.0.1:4000:4000"]`（只 bind localhost，不對外），然後 `ssh -L 4000:localhost:4000 ubuntu@<vm-ip>` 就能進 `http://localhost:4000`。第一次設好 virtual key 後可以拿掉。

---

## 本機測試 prod compose

要在本機驗證 prod compose 沒寫錯（不需要真的部到雲端）：

```bash
# 用本機 build 出來的 image 取代 GHCR pull
docker compose build  # 用一般的 docker-compose.yml 先 build 出來
docker tag llm-wiki-backend ghcr.io/<your-username>/llm-wiki-backend:latest
docker tag llm-wiki-frontend ghcr.io/<your-username>/llm-wiki-frontend:latest

# 起 prod compose（記得本機 .env 要有 GHCR_OWNER、DOMAIN、DUCKDNS_* 這些值）
# DOMAIN 本機測試時可以填 localhost，但 Caddy 會試圖申請 cert 失敗
# 想完全跳過 Caddy 測試 backend/frontend：
docker compose -f docker-compose.prod.yml up -d postgres litellm backend frontend
# 然後 frontend 直接從容器內 port mapping 暴露：
docker compose -f docker-compose.prod.yml run --service-ports frontend
```

> 平日開發還是用 `docker compose up`（吃 `docker-compose.yml`），本機 build 速度比較快、有 pgadmin 跟 ngrok。

---

## 安全性備註

1. **`.env` 永不進 git** — `.gitignore` 已涵蓋
2. **GitHub Actions secrets** — repo settings 加密儲存，workflow 不會印出來
3. **Postgres / LiteLLM 不對外** — 只有 Caddy port 80/443 開放
4. **Caddy 自動 HTTPS** — Let's Encrypt 證書 60 天自動續期
5. **SSH** — 建議關閉密碼登入，只允許 key：
   ```bash
   sudo nano /etc/ssh/sshd_config
   # PasswordAuthentication no
   sudo systemctl reload sshd
   ```
6. **fail2ban**（可選）— 防止 ssh 暴力破解：`sudo apt install fail2ban`

---

## 常見問題

**Q: build 在 Actions 跑很久（>15 分鐘）**
第一次必慢（QEMU 模擬 arm64 跑 pip install）。後續會快，因為 `cache-to: type=gha,mode=max`。

**Q: deploy step 失敗 "Permission denied (publickey)"**
- 確認 `DEPLOY_SSH_KEY` 是 **private key 全文**（含 `-----BEGIN/END-----` 那兩行）
- 確認對應的 public key 在 VM 的 `~/.ssh/authorized_keys`
- 確認 `DEPLOY_USER` 對 `/opt/llm-wiki` 有寫權限

**Q: Caddy 起不來，log 顯示 cert 申請失敗**
- 確認 80/443 port 從外部能連到（curl 本地 IP 從別的網路試）
- 確認 DuckDNS 的 IP 已經更新到當前 VM 的 IP（`dig your-subdomain.duckdns.org`）
- 第一次 boot 給 DuckDNS container 5 分鐘同步 IP 再起 Caddy

**Q: Oracle 砍我帳號了**
- 你的 backup 在 `/var/backups/llm-wiki/`，但帳號被砍 = VM 一起消失，所以你**現在**就應該把備份同步到別的地方
- 重開 → 重做 bootstrap，因為 image 在 GHCR、code 在 GitHub、`.env` 你應該也有本機 copy（沒有的話下次記得留）

**Q: idle 太久被回收**
寫個 cron 每天跑兩分鐘 stress：
```bash
sudo apt install stress-ng
# crontab：
0 4 * * * stress-ng --cpu 1 --timeout 120s
```
