#!/bin/bash
# gold-image-isolate.sh
# Run this FIRST on the replica hub immediately after SSH-ing in.
# Kills all services that phone home so the replica doesn't collide
# with 1beachhouse (which has the same identity).
#
# After this script runs:
#   - Cloudflare tunnel is dead (no tunnel flapping with 1beachhouse)
#   - MQTT broker connections are severed (no topic crossfire)
#   - Argus agent is stopped (no duplicate metrics)
#   - Hub controller is stopped (no duplicate API calls)
#   - Your SSH session stays alive (Ethernet LAN, no tunnel needed)
#
# Usage: sshpass -p jupyter2026 ssh root@<REPLICA_IP> 'bash -s' < gold-image-isolate.sh

set -e
echo "========================================="
echo "  REPLICA ISOLATION — killing outbound services"
echo "========================================="

# 1. Kill Cloudflare tunnel FIRST (biggest collision risk)
echo "[1/5] Stopping cloudflared..."
systemctl stop cloudflared.service 2>/dev/null || true
systemctl disable cloudflared.service 2>/dev/null || true
echo "  cloudflared stopped + disabled"

# 2. Stop Argus monitoring agent (sends metrics with same hub identity)
echo "[2/5] Stopping Argus agent..."
systemctl stop secureprotect-agent.service 2>/dev/null || true
systemctl stop secureprotect-vmagent.service 2>/dev/null || true
systemctl stop secureprotect-node-exporter.service 2>/dev/null || true
systemctl stop secureprotect-ip-reporter.timer 2>/dev/null || true
systemctl stop secureprotect-ip-reporter.service 2>/dev/null || true
echo "  Argus agent stack stopped"

# 3. Stop hub controller (makes API calls to cloud with same credentials)
echo "[3/5] Stopping hub controller + celery..."
systemctl stop jupyter-hub-controller.service 2>/dev/null || true
systemctl stop jupyter-hub-celery-camera.service 2>/dev/null || true
systemctl stop jupyter-hub-celery-beat.service 2>/dev/null || true
systemctl stop jupyter-hub-celery-automation.service 2>/dev/null || true
systemctl stop jupyter-hub-celery-restart-ring.service 2>/dev/null || true
echo "  Hub controller + all celery workers stopped"

# 4. Stop identity guard (would try to restore identity if we start cleaning)
echo "[4/5] Stopping identity guard..."
systemctl stop jupyter-identity-guard.timer 2>/dev/null || true
systemctl stop jupyter-identity-guard.service 2>/dev/null || true
echo "  Identity guard stopped"

# 5. Kill MQTT inside Docker (mosquitto brokers cloud-bound messages)
echo "[5/5] Stopping MQTT broker in Docker..."
docker stop mosquitto 2>/dev/null || true
echo "  MQTT broker stopped"

echo ""
echo "========================================="
echo "  ISOLATION COMPLETE"
echo "  Replica is now dark to the internet."
echo "  1beachhouse is unaffected."
echo "  You can now run the offboard script."
echo "========================================="
