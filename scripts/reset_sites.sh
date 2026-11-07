#!/usr/bin/env bash
# Recreate WebArena-Verified containers (discards all state changes) and wait until they serve.
set -euo pipefail
SITES=${*:-shopping shopping_admin reddit gitlab}
declare -A PORTS=([shopping]="-p 7770:80 -p 7771:8877" [shopping_admin]="-p 7780:80 -p 7781:8877"
                  [reddit]="-p 9999:80 -p 9998:8877" [gitlab]="-p 8023:8023 -p 8024:8877")
declare -A CHECK=([shopping]="http://localhost:7770/" [shopping_admin]="http://localhost:7780/admin"
                  [reddit]="http://localhost:9999/" [gitlab]="http://localhost:8023/users/sign_in")
for s in $SITES; do
  docker rm -f "webarena-verified-$s" >/dev/null 2>&1 || true
  docker run -d --name "webarena-verified-$s" ${PORTS[$s]} "am1n3e/webarena-verified-$s:0.1.0" >/dev/null
done
for s in $SITES; do
  for i in $(seq 1 120); do
    code=$(curl -s -o /dev/null -w "%{http_code}" -L --max-time 20 "${CHECK[$s]}" || true)
    [ "$code" = "200" ] && break; sleep 5
  done
  echo "$s -> $code"
done
