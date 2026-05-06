#!/bin/bash
# Extension to jupyter-disk-cleanup — caps log bloat that the existing
# cleanup didn't cover. Designed to be idempotent + safe to re-run every 6h.
# Pulled into the existing cleanup ExecStart chain. Pre-pilot 2026-05-06.
#
# What this prunes:
#   1. Docker container json.log archives (.log.1, .log.2, .log.3) older
#      than 14 days — Docker daemon log-rotation only keeps last N files
#      but doesn't time-prune them. After a noisy week the archived files
#      hold ~30MB per container forever otherwise.
#   2. systemd journal archives — enforces SystemMaxUse=20M which the
#      .conf alone is observed not to enforce reliably (saw 73MB on a
#      hub with 20M cap).
#   3. Dangling Docker images + stopped/exited test containers older
#      than 7 days — `docker system prune` with hard volume protection.
#
# What this DOES NOT touch:
#   - Frigate recordings (those are disk-pressure managed by Frigate itself)
#   - AI debug folders (those are managed by per-AI cleanup_old_debug_folders)
#   - /var/log/syslog (logrotate handles)
#   - Any /opt/secureprotect data (Argus owns)
#
# Run logged to syslog via journald.

set -u

log() { echo "[$(date -u +%FT%TZ)] disk-cleanup-extra: $*"; }

log "=== starting cleanup pass ==="

# 1) Time-prune Docker container json.log archives older than 14 days
DELETED=$(find /var/lib/docker/containers -name "*-json.log.[0-9]*" -mtime +14 2>/dev/null | wc -l)
find /var/lib/docker/containers -name "*-json.log.[0-9]*" -mtime +14 -delete 2>/dev/null || true
log "pruned $DELETED docker log archives (>14d)"

# 2) Enforce journald cap (the .conf SystemMaxUse alone isn't reliable)
journalctl --vacuum-size=20M >/dev/null 2>&1 || true
log "vacuumed journal to 20M"

# 3) Reap dangling images + stopped exited containers (NOT volumes — those
#    hold Postgres/Redis state)
docker system prune -f --filter "until=168h" --volumes=false >/dev/null 2>&1 || true
log "pruned dangling docker images + stopped containers older than 7d"

# 4) Telemetry — emit final disk-used % so we can grep journals for trend
USED=$(df -h / | awk 'NR==2 {print $5}')
log "post-cleanup disk used: $USED"
log "=== cleanup pass done ==="
