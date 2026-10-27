#!/usr/bin/env bash
# Testler:  scripts/run_tests.sh [unit|e2e|all]   (varsayılan: all)
#   unit : factory-api konteynerinde pytest (DB gerektirmeyen birim testleri)
#   e2e  : n8n webhook'u ile uçtan uca zincir (senaryo -> medya -> ses/altyazı -> render -> QC)
#          Mevcut Panel sağlayıcı ayarlarıyla çalışır; ücretsiz test için:
#          image=local_textcard, tts=espeak, video=none
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=${1:-all}

if [[ $MODE == unit || $MODE == all ]]; then
  docker cp tests "$(docker compose ps -q factory-api)":/tmp/ >/dev/null
  docker compose exec -T -u 0 factory-api sh -c \
    '/opt/venv/bin/python -c "import pytest" 2>/dev/null || /opt/venv/bin/pip install -q pytest; cd /srv && /opt/venv/bin/python -m pytest -q -p no:cacheprovider /tmp/tests'
fi

if [[ $MODE == e2e || $MODE == all ]]; then
  TOKEN=$(grep ^N8N_WEBHOOK_TOKEN= .env | cut -d= -f2)
  # Benzersiz konu: tekrar önleme mekanizması aynı senaryoyu ikinci kez kabul etmez
  STAMP=$(date +%s)
  python3 - "$STAMP" > /tmp/asf_e2e.json <<'PY'
import json, sys, uuid
d = json.load(open("tests/sample_script_tr_2.json"))
d["topic"] = f"E2E test {uuid.uuid4().hex}"   # benzerlik kontrolüne takılmayan benzersiz konu
d["script"]["description"] += f" Test {sys.argv[1]}."
d["script"]["scenes"][-1]["text"] += f" Test numarası {sys.argv[1][-4:]}."
print(json.dumps(d, ensure_ascii=False))
PY
  echo "E2E (n8n webhook) çalışıyor..."
  RES=$(curl -s -m 900 -X POST http://127.0.0.1:5678/webhook/asf-e2e -H "X-API-Key: $TOKEN" \
        -H 'Content-Type: application/json' --data @/tmp/asf_e2e.json)
  rm -f /tmp/asf_e2e.json
  echo "$RES" | python3 -m json.tool
  echo "$RES" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("qc_passed") else 1)' \
    && echo "E2E: BAŞARILI" || { echo "E2E: BAŞARISIZ"; exit 1; }
fi
