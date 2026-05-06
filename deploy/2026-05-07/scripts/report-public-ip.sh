#!/bin/bash
# PUSH_URL / CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET / HUB_NAME injected via systemd
PUSH_URL="${PUSH_URL:-http://brain.argus.jupyterdevices.com:8428}"
HUB_NAME="${HUB_NAME:-__RESET__}"
CF_HEADERS=()
if [ -n "$CF_ACCESS_CLIENT_ID" ] && [ -n "$CF_ACCESS_CLIENT_SECRET" ]; then
    CF_HEADERS=(-H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET")
fi
PUBLIC_IP=$(curl -s --connect-timeout 5 --max-time 10 https://api.ipify.org 2>/dev/null || curl -s --connect-timeout 5 --max-time 10 https://ifconfig.me 2>/dev/null || echo "")
[ -n "$PUBLIC_IP" ] && curl -s -X POST "${PUSH_URL}/api/v1/import/prometheus" "${CF_HEADERS[@]}" --data-binary "secureprotect_public_ip{location=\"${HUB_NAME}\",ip=\"${PUBLIC_IP}\"} 1" --connect-timeout 5 --max-time 10 2>/dev/null
