#!/usr/bin/env bash
# n8n'deki "YouTube OAuth2 (AI Shorts Factory)" kimliğine Client ID / Client secret yazar.
# Değerler ekrana veya diske yazılmaz; doğrudan n8n konteynerine aktarılır.
# Mevcut kapsam ayarları korunur. Sonra n8n'de "Sign in with Google" yapılır.
set -euo pipefail
cd "$(dirname "$0")/.."
DC="docker compose"
ID=asfYouTubeOAu001

export_cred() {
  $DC exec -T n8n sh -c "n8n export:credentials --id=$ID --decrypted --output=/tmp/yt.json >/dev/null 2>&1; cat /tmp/yt.json; rm -f /tmp/yt.json" </dev/null
}

current=$(export_cred) || { echo "YouTube kimliği bulunamadı. Önce scripts/n8n_bootstrap.sh çalıştırın."; exit 1; }

read -r -p "Client ID'yi yapıştırın (değiştirmek istemiyorsanız boş bırakıp Enter): " NEW_ID
read -r -s -p "Client secret'ı yapıştırıp Enter'a basın (ekranda görünmez): " NEW_SECRET
echo
[[ -n $NEW_SECRET ]] || { echo "Secret boş — hiçbir şey değiştirilmedi."; exit 1; }

printf '%s' "$current" | NEW_ID="$NEW_ID" NEW_SECRET="$NEW_SECRET" python3 -c '
import json, os, sys
c = json.load(sys.stdin)
d = c[0]["data"]
if os.environ["NEW_ID"].strip():
    d["clientId"] = os.environ["NEW_ID"].strip()
d["clientSecret"] = os.environ["NEW_SECRET"].strip()
print(json.dumps(c))
' | $DC exec -T n8n sh -c 'umask 077; cat > /tmp/c.json && n8n import:credentials --input=/tmp/c.json >/dev/null 2>&1; rc=$?; rm -f /tmp/c.json; exit $rc'
unset NEW_SECRET

export_cred | python3 -c '
import json, sys
d = json.load(sys.stdin)[0]["data"]
c, s = d.get("clientId", ""), d.get("clientSecret", "")
ok_id = c.endswith(".apps.googleusercontent.com")
ok_s = s.startswith("GOCSPX-") and len(s) >= 30
print("Client ID :", "doğru görünüyor" if ok_id else "HATALI — .apps.googleusercontent.com ile bitmeli")
print("Secret    :", f"kaydedildi ({len(s)} karakter)" if ok_s else f"ŞÜPHELİ ({len(s)} karakter, GOCSPX- ile başlamalı)")
sys.exit(0 if ok_id and ok_s else 1)
' && echo "Tamam. n8n sayfasını yenileyin (Cmd+R) ve YouTube kaydında yalnızca 'Sign in with Google'a basın."
