#!/usr/bin/env bash
# Panel şifresini bcrypt ile hash'leyip .env içine yazar. Şifre ekrana/loga yazılmaz.
set -euo pipefail
cd "$(dirname "$0")/.."
read -r -s -p "Yeni panel şifresi (en az 12 karakter): " PW; echo
[[ ${#PW} -ge 12 ]] || { echo "Şifre çok kısa"; exit 1; }
HASH=$(PW="$PW" python3 -c 'import os,bcrypt;print(bcrypt.hashpw(os.environ["PW"].encode(),bcrypt.gensalt(12)).decode())' 2>/dev/null \
  || docker run --rm -e PW="$PW" python:3.12-slim sh -c 'pip -q install bcrypt >/dev/null 2>&1 && python -c "import os,bcrypt;print(bcrypt.hashpw(os.environ[\"PW\"].encode(),bcrypt.gensalt(12)).decode())"')
grep -v '^PANEL_PASSWORD_HASH=' .env > .env.tmp
echo "PANEL_PASSWORD_HASH='$HASH'" >> .env.tmp
mv .env.tmp .env && chmod 600 .env
echo "Panel şifresi ayarlandı. Uygulamak için: docker compose up -d factory-api"
