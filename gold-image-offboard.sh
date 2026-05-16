#!/bin/bash
# gold-image-offboard.sh
# Modified offboard for gold image creation.
# SKIPS all cloud/remote phases so 1beachhouse's Django record is untouched.
#
# What's SKIPPED (vs reset_hub.sh):
#   - Phase 2a: Argus brain SSH removal (would affect 1beachhouse's tunnel)
#   - Phase 2b: Cloud DELETE /hub/removed (would delete 1beachhouse's record!)
#   - Phase 2c: Argus dashboard offboard (same identity as 1beachhouse)
#   - Phase 2d: Halo factory reset (no Halos on replica)
#   - Phase 8: WiFi disconnect + reboot (we want to keep SSH alive for imaging)
#
# What's KEPT (local cleanup only):
#   - Phase 0: Unlock OTA-protected files
#   - Phase 1: Load state (for reference only)
#   - Phase 3: Stop services
#   - Phase 4: Clean identity vars + user data + PostgreSQL
#   - Phase 4.5: Gold-image hygiene
#   - Phase 5: Docker volume cleanup + secret regeneration
#   - Phase 6: Restart BLE for setup mode
#   - Phase 7: Stop tunnel (already stopped by isolate script)
#
# PRE-REQUISITE: Run gold-image-isolate.sh first!
#
# Usage: sshpass -p jupyter2026 ssh root@<REPLICA_IP> 'bash -s' < gold-image-offboard.sh

ENV_FILE="/root/jupyter-hub-controller/.env"
CONTAINER_ENV_FILE="/root/jupyter-container/.env"
COMPOSE_DIR="/root/jupyter-container"
IDENTITY_BACKUP_DIR="/root/.jupyter-identity-backup"

IDENTITY_VARS="DEVICE_NAME DEVICE_SECRET HUB_SECRET HUB_BASIC_AUTH HUB_NAME REMOTE_HOST TUNNEL_TOKEN SSH_TUNNEL_TOKEN LOCAL_IP END_TASK_BLE CONNECTION_STATUS LIVE_ACTIVITY_START_TOKEN HALO_CHARGING_START_TOKEN LIVE_ACTIVITY_TOKEN_REFRESHED FCM_REGISTRATION_IDS APNS_DEVICE_TOKENS"

# Disable reset service to prevent reboot loops
sudo systemctl disable jupyter-hub-reset.service 2>/dev/null || true

# Signal to identity guard that this is intentional
touch /tmp/.jupyter-reset-intent

# Remove immutable flag from docker-compose.yml
chattr -i "$COMPOSE_DIR/docker-compose.yml" 2>/dev/null || true

echo "====== GOLD IMAGE OFFBOARD START ======"
echo "NOTE: All cloud phases SKIPPED. 1beachhouse record is SAFE."
echo ""

# ===============================
# PHASE 0: UNLOCK OTA-PROTECTED FILES
# ===============================
echo "PHASE 0: Unlocking OTA-protected files..."
chattr -i -R /usr/local/bin/ /root/jupyter-container/ /opt/ /etc/systemd/system/ /etc/docker/ 2>/dev/null || true
echo "  Done"

# ===============================
# PHASE 1: LOAD STATE (read-only, for logging)
# ===============================
echo "PHASE 1: Loading current identity (for logging only)..."
if [ -f "$ENV_FILE" ]; then
    CURRENT_DEVICE=$(grep "^DEVICE_NAME=" "$ENV_FILE" | cut -d= -f2 | tr -d '\r')
    echo "  Current identity: ${CURRENT_DEVICE:-EMPTY}"
fi

# ===============================
# PHASE 2: SKIPPED (cloud operations)
# ===============================
echo ""
echo "PHASE 2: SKIPPED — no cloud DELETE, no Argus brain, no Halo reset"
echo "  1beachhouse Django record: PROTECTED"
echo ""

# ===============================
# PHASE 3: STOP SERVICES (most already stopped by isolate script)
# ===============================
echo "PHASE 3: Stopping remaining services..."
sudo systemctl stop jupyter-identity-guard.timer 2>/dev/null || true
sudo systemctl stop jupyter-identity-guard.service 2>/dev/null || true
sudo systemctl stop secureprotect-agent.service 2>/dev/null || true
if [ -f /opt/secureprotect-agent/docker-compose.yml ]; then
    cd /opt/secureprotect-agent && sudo docker compose down 2>/dev/null || true
    cd /root
fi
sudo docker volume rm secureprotect-agent_agent_data 2>/dev/null || true
sudo docker volume rm secureprotect-agent_tunnel_data 2>/dev/null || true
sudo systemctl stop jupyter-hub-controller.service 2>/dev/null || true
echo "  Done"

# ===============================
# PHASE 4: CLEAN LOCAL STATE
# ===============================
echo "PHASE 4: Cleaning identity vars..."
for envfile in "$ENV_FILE" "$CONTAINER_ENV_FILE"; do
    if [ -f "$envfile" ]; then
        for var in $IDENTITY_VARS; do
            sed -i "/^${var}=/d" "$envfile"
        done
        if grep -q "^HUB_USER_ID=" "$envfile"; then
            sed -i 's/^HUB_USER_ID=.*/HUB_USER_ID=0/' "$envfile"
        else
            echo "HUB_USER_ID=0" >> "$envfile"
        fi
        echo "  Cleaned: $envfile"
    fi
done

# Clear identity guard backup
if [ -d "$IDENTITY_BACKUP_DIR" ]; then
    echo "  Cleaning identity guard backup..."
    rm -f "$IDENTITY_BACKUP_DIR/.env.identity" 2>/dev/null || true
    for bak in "$IDENTITY_BACKUP_DIR"/.env*; do
        [ -f "$bak" ] || continue
        for var in $IDENTITY_VARS; do
            sed -i "/^${var}=/d" "$bak" 2>/dev/null || true
        done
        if grep -q "^HUB_USER_ID=" "$bak"; then
            sed -i 's/^HUB_USER_ID=.*/HUB_USER_ID=0/' "$bak"
        fi
    done
fi

# Remove credential files
for f in /root/jupyter-container/credentials/hub_credentials.json \
         /root/jupyter-container/credentials/iot_credentials.json \
         /root/jupyter-container/credentials/tunnel*.json; do
    [ -f "$f" ] && rm -f "$f" && echo "  Removed: $f"
done

# Clean upload folder
UPLOAD_DIR="/root/jupyter-hub-controller/upload"
if [ -d "$UPLOAD_DIR" ]; then
    sudo rm -rf "$UPLOAD_DIR"/*
else
    mkdir -p "$UPLOAD_DIR" && sudo chmod a+w "$UPLOAD_DIR"
fi

# MediaMTX: strip camera paths
echo "  Resetting MediaMTX camera paths..."
MEDIAMTX_CONFIG="/root/mediamtx/mediamtx.yml"
if [ -f "$MEDIAMTX_CONFIG" ]; then
    sed -i '/^paths:/,$d' "$MEDIAMTX_CONFIG"
    echo "" >> "$MEDIAMTX_CONFIG"
    echo "paths:" >> "$MEDIAMTX_CONFIG"
fi

# Ring-MQTT: clean bind-mount data
rm -rf "$COMPOSE_DIR/ring-mqtt-data/ring-state.json" 2>/dev/null || true
rm -rf "$COMPOSE_DIR/ring-mqtt-data/go2rtc.yaml" 2>/dev/null || true

# Frigate: reset config
FRIGATE_DIR="$COMPOSE_DIR/frigate"
CONFIG_DIR="$FRIGATE_DIR/config"
SRC_FILE="/root/frigate_config_default.yaml"
DEST_FILE="$CONFIG_DIR/config.yaml"
echo "  Resetting Frigate configuration..."
sudo mkdir -p "$CONFIG_DIR"
if [ -f "$SRC_FILE" ]; then
    sudo cp "$SRC_FILE" "$DEST_FILE"
    sudo chmod 644 "$DEST_FILE"
fi
sudo chmod -R 777 "$FRIGATE_DIR"

# Home Assistant: full reset
echo "  Resetting Home Assistant..."
HASS_DIR="$COMPOSE_DIR/hass"
HASS_CONFIG_DIR="$HASS_DIR/config"
if [ -d "$HASS_CONFIG_DIR" ]; then
    rm -f "$HASS_CONFIG_DIR/automations.yaml"
    rm -f "$HASS_CONFIG_DIR/scripts.yaml"
    rm -f "$HASS_CONFIG_DIR/scenes.yaml"
    echo "[]" > "$HASS_CONFIG_DIR/automations.yaml"
    echo "[]" > "$HASS_CONFIG_DIR/scripts.yaml"
    echo "[]" > "$HASS_CONFIG_DIR/scenes.yaml"
    rm -rf "$HASS_CONFIG_DIR/.storage"
    rm -f "$HASS_CONFIG_DIR/home-assistant_v2.db"
    rm -f "$HASS_CONFIG_DIR/home-assistant_v2.db-*"
    rm -f "$HASS_CONFIG_DIR/home-assistant.log"* 2>/dev/null || true
fi

# Clean PostgreSQL user data
echo "  Cleaning PostgreSQL user data tables..."
USER_DATA_TABLES=(
    "event_event"
    "camera_camerasettingzone"
    "camera_camerasetting"
    "camera_camera"
    "ring_ringaccount"
    "alarm_alarmdeviceconfig"
    "alarm_alarmdevice"
    "automation_alarmsettings"
    "automation_garage_garagedoorsettings"
    "meross_merossdevice"
    "meross_merosscloudaccount"
    "face_training_facetraining"
    "facial_facial"
    "suggested_facial_suggestedfacial"
    "vehicle_vehicle"
    "gdrive_backup_backuprecord"
    "gdrive_backup_backupschedule"
    "gdrive_backup_googledriveaccount"
    "external_device_externaldevice"
    "cloudflare_turn_turn"
)
for tbl in "${USER_DATA_TABLES[@]}"; do
    sudo docker exec postgres psql -U postgres -d hub_controller -c "DELETE FROM $tbl CASCADE;" 2>/dev/null || true
done
echo "  PostgreSQL cleaned"

# ===============================
# PHASE 4.5: GOLD-IMAGE HYGIENE
# ===============================
echo "PHASE 4.5: Gold-image hygiene..."

# Auto-backups
rm -f /root/jupyter-hub-controller/.env.backup-auto-* 2>/dev/null || true
rm -f /root/jupyter-hub-controller/.env.pre-test-backup 2>/dev/null || true

# Stale .env from previous hub-controller layout
rm -f /root/jupyter-hub-controller.env 2>/dev/null || true
rm -f /root/jupyter-hub-controller-a/.env 2>/dev/null || true
rm -f /root/jupyter-hub-controller-a/local_env 2>/dev/null || true

# Stale container .env backups
rm -f /root/jupyter-container/.env.bak-* 2>/dev/null || true

# Frigate config backups (camera RTSP creds)
rm -f /root/jupyter-container/frigate/config/config.yaml.bak* 2>/dev/null || true
rm -f /root/jupyter-container/frigate/config/backup_config.yaml 2>/dev/null || true
rm -f /root/jupyter-container/frigate/config/backup.db 2>/dev/null || true

# MediaMTX config backups
rm -f /root/mediamtx/mediamtx.yml.bak* 2>/dev/null || true

# AI source code .bak files
find /root/jupyter-container -maxdepth 3 -name "*.py.bak*" -delete 2>/dev/null || true
find /root/jupyter-container -maxdepth 3 -name "*.py.bak-pre-*" -delete 2>/dev/null || true

# docker-compose.yml backups
find /root/jupyter-container -maxdepth 1 -name "docker-compose.yml.bak-*" -delete 2>/dev/null || true

# HAProxy config backups
find /root/jupyter-container/haproxy -name "*.bak*" -delete 2>/dev/null || true
find /root/jupyter-container/haproxy -name "*.broken-*" -delete 2>/dev/null || true

# Stale duplicate directories
rm -rf /root/jupyter-hub-controller-a 2>/dev/null || true

# .env.systemd files
rm -f /root/jupyter-hub-controller/.env.systemd 2>/dev/null || true

# Argus monitoring identity reset
if [ -f /opt/secureprotect/config/agent.json ]; then
    sed -i 's/"hub_name":"[^"]*"/"hub_name":""/' /opt/secureprotect/config/agent.json
fi
if [ -f /opt/secureprotect/config/scrape.yml ]; then
    sed -i "s/hub_name: .*/hub_name: ''/" /opt/secureprotect/config/scrape.yml
    sed -i "s/location: .*/location:/" /opt/secureprotect/config/scrape.yml
fi
if [ -f /etc/systemd/system/secureprotect-vmagent.service ]; then
    sed -i 's/-remoteWrite.label=location=[^ ]*/-remoteWrite.label=location=__RESET__/' /etc/systemd/system/secureprotect-vmagent.service
fi
if [ -f /etc/systemd/system/secureprotect-ip-reporter.service ]; then
    sed -i 's/^Environment=HUB_NAME=.*/Environment=HUB_NAME=/' /etc/systemd/system/secureprotect-ip-reporter.service
fi
if [ -f /opt/secureprotect/bin/report-public-ip.sh ]; then
    sed -i "s/HUB_NAME:-[^}]*/HUB_NAME:-__RESET__/" /opt/secureprotect/bin/report-public-ip.sh 2>/dev/null || true
fi

# Additional cruft from prior sessions
rm -f /root/cloudflared.log /root/env.zip /root/local_env 2>/dev/null || true
rm -f /root/reset_hub.sh.bak-* 2>/dev/null || true
rm -rf /root/__MACOSX 2>/dev/null || true
rm -rf /root/jupyter-ble-controller-a /root/jupyter-ble-controller-old 2>/dev/null || true
rm -rf /root/jupyter-hub-controller-b /root/jupyter-hub-controller-old 2>/dev/null || true
rm -rf /root/voice_ai_v3 /root/jupyter-container/voice_ai_v3 2>/dev/null || true
rm -f /root/jupyter-hub-controller/.env_backup 2>/dev/null || true
find /root/jupyter-hub-controller -maxdepth 4 \( -name "*.bak*" -o -name "*.backup" \) -delete 2>/dev/null || true
rm -rf /root/.vscode-server 2>/dev/null || true
rm -f /root/.env.identity.bak 2>/dev/null || true
rm -f /root/.bash_history /root/.lesshst /root/.zsh_history 2>/dev/null || true
rm -f /root/.ssh/known_hosts /root/.ssh/known_hosts.old 2>/dev/null || true
rm -f /root/.gitconfig 2>/dev/null || true

echo "  Gold-image hygiene complete"

# ===============================
# PHASE 5: DOCKER REBUILD
# ===============================
echo "PHASE 5: Docker cleanup..."
cd "$COMPOSE_DIR"
sudo docker compose down --timeout 30 || true

# Frigate storage cleanup
echo "  Cleaning Frigate storage..."
STORAGE_DIR="$COMPOSE_DIR/frigate/storage"
for folder in clips exports recordings debugs; do
    TARGET="$STORAGE_DIR/$folder"
    if [ -d "$TARGET" ]; then
        find "$TARGET" -type f -delete 2>/dev/null || true
        find "$TARGET" -mindepth 1 -type d -empty -delete 2>/dev/null || true
    else
        mkdir -p "$TARGET"
    fi
done

# Clean user-specific media
for media_dir in "$COMPOSE_DIR/alarm_audio" "$COMPOSE_DIR/voice_ai_data"; do
    if [ -d "$media_dir" ]; then
        sudo rm -rf "$media_dir"/* 2>/dev/null || true
    fi
done

# Generate service secrets for fresh database init
echo "  Generating fresh service secrets..."
DEFAULT_DB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || echo "secureprotect-$(date +%s)")
DEFAULT_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))' 2>/dev/null || echo "django-secret-$(date +%s)")
DEFAULT_SVC_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || echo "svcpass-$(date +%s)")

for envfile in "$ENV_FILE" "$CONTAINER_ENV_FILE"; do
    [ -f "$envfile" ] || continue
    sed -i "s/^DB_PASSWORD=.*/DB_PASSWORD=${DEFAULT_DB_PASSWORD}/" "$envfile"
    sed -i "s/^DB_HOST=.*/DB_HOST=127.0.0.1/" "$envfile"
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${DEFAULT_SECRET_KEY}/" "$envfile"
    for svc_var in MQTT_PASSWORD MQTT_CONTROLLER_PASSWORD MQTT_AI_CONTROLLER_PASSWORD \
                   MQTT_FRIGATE_PASSWORD MQTT_HASS_PASSWORD MQTT_RING_CAMERA_PASSWORD \
                   HASS_PASSWORD FRIGATE_PASSWORD SCRYPTED_PASSWORD; do
        sed -i "s/^${svc_var}=.*/${svc_var}=${DEFAULT_SVC_PASSWORD}/" "$envfile"
    done
done

# Remove Docker volumes (postgres + redis)
echo "  Removing Docker volumes..."
for vol in jupyter-container_postgres-vlm jupyter-container_redis_data; do
    sudo docker volume rm "$vol" 2>/dev/null || echo "  Volume $vol not found"
done

# iptables: allow Docker containers to reach Django on port 8000
if sudo iptables -L INPUT -n 2>/dev/null | grep -q "DROP.*tcp.*dpt:8000"; then
    sudo iptables -I INPUT 1 -s 172.16.0.0/12 -p tcp --dport 8000 -j ACCEPT 2>/dev/null || true
fi

cd /root

# ===============================
# PHASE 6: POST-RESET VERIFICATION + BLE
# ===============================
echo "PHASE 6: Verifying clean state..."
VERIFICATION_FAILED=false

for envfile in "$ENV_FILE" "$CONTAINER_ENV_FILE"; do
    [ -f "$envfile" ] || continue
    for var in $IDENTITY_VARS; do
        if grep -q "^${var}=" "$envfile"; then
            echo "  STALE: $var found in $envfile — removing"
            sed -i "/^${var}=/d" "$envfile"
            VERIFICATION_FAILED=true
        fi
    done
    CURRENT_HUB_ID=$(grep "^HUB_USER_ID=" "$envfile" 2>/dev/null | cut -d= -f2)
    if [ -n "$CURRENT_HUB_ID" ] && [ "$CURRENT_HUB_ID" != "0" ]; then
        echo "  STALE: HUB_USER_ID=$CURRENT_HUB_ID — setting to 0"
        sed -i 's/^HUB_USER_ID=.*/HUB_USER_ID=0/' "$envfile"
        VERIFICATION_FAILED=true
    fi
done

if [ "$VERIFICATION_FAILED" = "true" ]; then
    echo "  WARNING: Stale vars were found and removed during verification"
else
    echo "  Verification PASSED — all identity vars clean"
fi

# Restart BLE for setup mode
echo "  Restarting BLE for setup mode..."
sudo systemctl stop jupyter-ble.service 2>/dev/null || true
sudo systemctl enable jupyter-ble.service 2>/dev/null || true
sudo systemctl start jupyter-ble.service 2>/dev/null || true

# Re-enable identity guard + haproxy heal for post-imaging
for svc in jupyter-identity-guard.timer haproxy-heal.timer; do
    sudo systemctl enable "$svc" 2>/dev/null || true
done

# Clean BLE credential caches
rm -f /root/jupyter-ble-controller/wifi_credentials.json 2>/dev/null || true
rm -f /root/jupyter-ble-controller/iot_credentials.json 2>/dev/null || true

# ===============================
# PHASE 7+8: SKIPPED (no tunnel kill, no WiFi disconnect, no reboot)
# ===============================
echo ""
echo "========================================="
echo "  GOLD IMAGE OFFBOARD COMPLETE"
echo "========================================="
echo ""
echo "  Identity vars: WIPED"
echo "  User data: WIPED"
echo "  Docker volumes: REMOVED"
echo "  1beachhouse cloud record: UNTOUCHED"
echo "  BLE: ADVERTISING (setup mode)"
echo ""
echo "  SSH is still alive. Next steps:"
echo "    1. Verify BLE advertising: hcitool lescan"
echo "    2. (Optional) Test onboard from Flutter app"
echo "    3. Sync any missing Docker images from 1beachhouse"
echo "    4. Clean shutdown: shutdown -h now"
echo "    5. Image the eMMC/SD card from your Mac"
echo "========================================="
