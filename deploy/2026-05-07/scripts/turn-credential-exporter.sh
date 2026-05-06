#!/bin/bash
# 2026-05-07: exposes TURN credential rotation freshness so Argus can
# group hubs by "last rotation > N hours ago" and detect rotation failures.
# Reads cloudflare_turn_turn table (one row per hub) directly.

set -u
OUT="/var/lib/node_exporter/textfile_collector/turn_credential.prom"
TMP="${OUT}.tmp"

ROW=$(docker exec postgres psql -U postgres -d hub_controller -tAc \
    "SELECT EXTRACT(EPOCH FROM updated_at)::int, EXTRACT(EPOCH FROM NOW() - updated_at)::int, uid, name FROM cloudflare_turn_turn ORDER BY updated_at DESC LIMIT 1;" 2>/dev/null)

UPDATED_AT=$(echo "$ROW" | cut -d"|" -f1)
AGE_S=$(echo "$ROW" | cut -d"|" -f2)
TURN_UID=$(echo "$ROW" | cut -d"|" -f3)
TURN_NAME=$(echo "$ROW" | cut -d"|" -f4)

cat > "$TMP" <<EOF
# HELP turn_credential_last_rotation_unixtime When TURN credential was last rotated (cloudflare_turn_turn.updated_at)
# TYPE turn_credential_last_rotation_unixtime gauge
turn_credential_last_rotation_unixtime{turn_name="${TURN_NAME:-unknown}",turn_uid="${TURN_UID:-unknown}"} ${UPDATED_AT:-0}
# HELP turn_credential_age_seconds Seconds since last TURN rotation (alert if >7d which exceeds 2d cadence)
# TYPE turn_credential_age_seconds gauge
turn_credential_age_seconds{turn_name="${TURN_NAME:-unknown}",turn_uid="${TURN_UID:-unknown}"} ${AGE_S:-0}
# HELP turn_credential_present 1 if TURN cred row exists, 0 otherwise
# TYPE turn_credential_present gauge
turn_credential_present{turn_name="${TURN_NAME:-unknown}",turn_uid="${TURN_UID:-unknown}"} $([ -n "${UPDATED_AT:-}" ] && echo 1 || echo 0)
EOF
mv "$TMP" "$OUT"
