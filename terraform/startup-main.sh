#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/yt2mf"
ENV_DIR="/etc/yt2mf"
ENV_FILE="$ENV_DIR/yt2mf.env"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  ffmpeg \
  curl \
  git \
  ca-certificates \
  unzip \
  gnupg
# Google Cloud CLI
if ! command -v gcloud >/dev/null 2>&1; then
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg

  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list

  apt-get update
  apt-get install -y google-cloud-cli
fi
# yt-dlp and Deno
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
chmod 0755 /usr/local/bin/yt-dlp
curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

id yt2mf >/dev/null 2>&1 || useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin yt2mf
mkdir -p "$APP_DIR" "$ENV_DIR" /var/lib/yt2mf/jobs
rm -rf "$APP_DIR"/*
git clone --depth 1 "${repo_url}" "$APP_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# Cloud SQL Auth Proxy
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64) PROXY_ARCH="amd64" ;;
  arm64) PROXY_ARCH="arm64" ;;
  *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac
curl -L "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.18.2/cloud-sql-proxy.linux.$${PROXY_ARCH}" -o /usr/local/bin/cloud-sql-proxy
chmod 0755 /usr/local/bin/cloud-sql-proxy

cat > /etc/systemd/system/yt2mf-cloud-sql-proxy.service <<SERVICE
[Unit]
Description=yt2mf Cloud SQL Auth Proxy
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/bin/cloud-sql-proxy --address 127.0.0.1 --port 5432 ${sql_instance}
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now yt2mf-cloud-sql-proxy

DB_PASSWORD="$(gcloud secrets versions access latest --secret='${db_secret}')"
BOT_TOKEN="$(gcloud secrets versions access latest --secret='${bot_secret}')"

cat > "$ENV_FILE" <<ENV
GCP_PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
PUBSUB_TOPIC=video-jobs
PUBSUB_SUBSCRIPTION=video-workers
DATABASE_URL=postgresql://${db_user}:$${DB_PASSWORD}@127.0.0.1:5432/${db_name}
BOT_TOKEN=$${BOT_TOKEN}
DOWNLOAD_DIR=/var/lib/yt2mf/jobs
YTDLP_PATH=/usr/local/bin/yt-dlp
DENO_PATH=/usr/local/bin/deno
MFCMD_PATH=$APP_DIR/mfcmd.py
MEDIAFIRE_SESSION=/var/lib/yt2mf/session.json
COOKIES_PATH=/var/lib/yt2mf/youtube-cookies.txt
GCS_BUCKET=${bucket}
WORKER_MAX_JOBS=1
MAX_CONCURRENT_DOWNLOADS=1
MAX_CONCURRENT_UPLOADS=1
MAX_CONCURRENT_COMPRESSION=1
JOB_TIMEOUT=7200
TELEGRAM_TIMEOUT=7200
JOB_LEASE_SECONDS=1800
JOB_HEARTBEAT_SECONDS=300
MAX_JOB_ATTEMPTS=3
ENV

chmod 600 "$ENV_FILE"
chown -R yt2mf:yt2mf "$APP_DIR" /var/lib/yt2mf
install -m 0644 "$APP_DIR/systemd/yt2mf-main.service" /etc/systemd/system/yt2mf-main.service
systemctl daemon-reload
systemctl enable --now yt2mf-main
