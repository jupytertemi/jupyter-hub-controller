#!/bin/bash
# /usr/local/bin/jupyter-hub-self-heal.sh
#
# Boot-time self-heal for jupyter hubs. Restores canonical state.
# Idempotent. Safe to run unlimited times. No hardcoded values.
#
# Canonical state contract:
#   - cloudflared.service is the ONE cloudflared owner (image-baked).
#   - No parallel cloudflared services may exist.
#   - /root/start_cloudflared.sh must not exist (deprecated helper).
#   - cloudflared.service must be enabled at boot.
#
# This script makes every hub re-converge to this contract on every boot,
# protecting the fleet from drift introduced by power cuts, support fixes,
# or partial setup runs.

set -uo pipefail

log() { logger -t jupyter-self-heal "$@"; }

# 1. Remove any parallel cloudflared services that may have drifted in.
PARALLEL_SERVICES=(
  "cloudflared-tunnel.service"
  "cloudflared-quick.service"
)
for svc in "${PARALLEL_SERVICES[@]}"; do
  if [ -f "/etc/systemd/system/$svc" ]; then
    systemctl stop "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/$svc"
    log "removed parallel service $svc"
  fi
done

# 2. Remove deprecated helper script.
if [ -f /root/start_cloudflared.sh ]; then
  rm -f /root/start_cloudflared.sh
  log "removed deprecated /root/start_cloudflared.sh"
fi

# 3. Ensure cloudflared.service is enabled at boot.
if systemctl list-unit-files | grep -qE "^cloudflared\.service[[:space:]]+disabled"; then
  systemctl enable cloudflared.service 2>/dev/null || true
  log "re-enabled cloudflared.service at boot"
fi

# 4. Kill orphan cloudflared processes not owned by the canonical service.
EXPECTED_PID="$(systemctl show cloudflared.service -p MainPID --value 2>/dev/null)"
for pid in $(pgrep -f "/usr/bin/cloudflared " 2>/dev/null); do
  [ "$pid" = "$EXPECTED_PID" ] && continue
  if grep -q "/system.slice/cloudflared.service" "/proc/$pid/cgroup" 2>/dev/null; then
    continue
  fi
  log "killing orphan cloudflared pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true
done

# 5. Apply pending systemd changes.
systemctl daemon-reload 2>/dev/null || true

log "self-heal pass complete"
exit 0
