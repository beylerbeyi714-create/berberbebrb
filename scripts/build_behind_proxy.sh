#!/usr/bin/env bash
# TLS denetimli proxy arkasında (kurumsal ağ / bulut sandbox) imaj derleme.
# Normal sunucuda gerekmez: `docker compose build` yeterlidir.
#   HTTPS_PROXY=http://host:port EXTRA_CA_FILE=/path/ca.crt scripts/build_behind_proxy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${EXTRA_CA_FILE:?EXTRA_CA_FILE gerekli}"
for svc in factory:factory-api video-worker:video-worker; do
  dir=${svc%%:*}; name=${svc##*:}
  for attempt in 1 2 3 4 5; do   # Docker Hub 429 (hız sınırı) için geri çekilmeli tekrar
    if docker build --network host --build-arg HTTPS_PROXY="${HTTPS_PROXY:-}" --build-arg https_proxy="${HTTPS_PROXY:-}" \
        --secret id=extra_ca,src="$EXTRA_CA_FILE" -t "ai-shorts-factory-$name" "services/$dir"; then break; fi
    [[ $attempt == 5 ]] && exit 1
    echo "derleme başarısız, $((attempt * 30)) sn sonra tekrar ($attempt/5)"; sleep $((attempt * 30))
  done
done
