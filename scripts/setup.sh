#!/usr/bin/env bash
# İlk kurulum: .env üretir (rastgele gizli değerler), veri klasörlerini hazırlar.
# Var olan .env dosyasının üzerine YAZMAZ.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env zaten var — dokunulmadı."
else
  cp .env.example .env
  gen() { openssl rand -hex "${1:-24}"; }
  for k in POSTGRES_PASSWORD N8N_DB_PASSWORD FACTORY_DB_PASSWORD FACTORY_API_TOKEN WORKER_API_TOKEN N8N_WEBHOOK_TOKEN; do
    sed -i.bak "s|^$k=.*|$k=$(gen 24)|" .env
  done
  sed -i.bak "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=$(gen 32)|" .env
  sed -i.bak "s|^SESSION_SECRET=.*|SESSION_SECRET=$(gen 32)|" .env
  rm -f .env.bak && chmod 600 .env
  echo ".env oluşturuldu (izin 600). N8N_ENCRYPTION_KEY değerini güvenli bir yere yedekleyin:"
  echo "bu anahtar kaybolursa n8n'deki kimlik bilgileri (YouTube OAuth dahil) çözülemez."
fi

mkdir -p data/media data/renders data/backups data/tmp
# factory-api, video-worker ve n8n konteynerleri uid 1000 ile çalışır
if [[ $(uname) == Darwin ]]; then
  # macOS (Docker Desktop): sahiplik eşlenmez; klasörün yazılabilir olması yeterli
  chmod -R a+rwX data 2>/dev/null || sudo chmod -R a+rwX data
else
  chown -R 1000:1000 data 2>/dev/null || sudo chown -R 1000:1000 data
fi
echo "Sonraki adım: scripts/set_panel_password.sh  ardından  docker compose up -d --build"
