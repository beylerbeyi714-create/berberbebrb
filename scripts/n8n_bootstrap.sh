#!/usr/bin/env bash
# n8n'i otomatik yapılandırır:
#   1) sahip (owner) hesabını oluşturur (yoksa)
#   2) kimlik bilgilerini (.env'den) içe aktarır: Factory API Key, n8n Webhook Key, YouTube OAuth2
#   3) 11 iş akışını içe aktarır ve yayınlar (aktif eder)
# Tekrar çalıştırmak güvenlidir: aynı kimliklerle üzerine yazar.
#
#   N8N_OWNER_EMAIL=... N8N_OWNER_PASSWORD=... scripts/n8n_bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DC="docker compose"
N8N=http://127.0.0.1:5678

until curl -sf $N8N/healthz >/dev/null; do sleep 2; done

# 1) owner
if curl -s $N8N/rest/settings | grep -q '"showSetupOnFirstLoad":true'; then
  : "${N8N_OWNER_EMAIL:?N8N_OWNER_EMAIL gerekli}"; : "${N8N_OWNER_PASSWORD:?N8N_OWNER_PASSWORD gerekli (min 8, büyük harf + rakam)}"
  body=$(python3 -c 'import json,os;print(json.dumps({"email":os.environ["N8N_OWNER_EMAIL"],"firstName":"Shorts","lastName":"Factory","password":os.environ["N8N_OWNER_PASSWORD"]}))')
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST $N8N/rest/owner/setup -H 'Content-Type: application/json' -d "$body")
  [[ $code == 200 ]] && echo "n8n sahip hesabı oluşturuldu" || { echo "owner setup HTTP $code"; exit 1; }
else
  echo "n8n sahip hesabı zaten var"
fi

# 2) kimlik bilgileri + 3) iş akışları
python3 n8n/build_workflows.py >/dev/null
# YouTube kimliği yalnızca ilk kurulumda oluşturulur; sonra OAuth token'ları korunur
YT_FLAG=--with-youtube
if $DC exec -T n8n n8n export:credentials --id=asfYouTubeOAu001 --output=/tmp/yt.json >/dev/null 2>&1; then
  YT_FLAG=""; $DC exec -T n8n rm -f /tmp/yt.json
fi
python3 n8n/build_workflows.py --credentials $YT_FLAG | $DC exec -T n8n sh -c \
  'umask 077; cat > /tmp/c.json && n8n import:credentials --input=/tmp/c.json; rc=$?; rm -f /tmp/c.json; exit $rc'
$DC exec -T n8n n8n import:workflow --separate --input=/import/workflows

for f in n8n/workflows/*.json; do
  id=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['id'])" "$f")
  $DC exec -T n8n n8n publish:workflow --id="$id" >/dev/null && echo "yayınlandı: $(basename "$f")"
done
$DC restart n8n >/dev/null
until curl -sf $N8N/healthz >/dev/null; do sleep 2; done
echo "n8n hazır: $N8N  (YouTube OAuth bağlantısı için docs/YOUTUBE_OAUTH.md)"
