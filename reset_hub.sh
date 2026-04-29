#!/bin/bash
# SecureProtect Hub Reset Script
# Called by Celery via jupyter-hub-reset.service (from Django POST /api/resetting)
# Runs as local process — WiFi disconnect does NOT kill this script.
#
# ORDERING RULES:
#   Phase 0: Unlock OTA-protected files
#   Phase 1: Load state (need credentials for cloud delete)
#   Phase 2: Network ops (need WiFi + credentials)
#   Phase 3: Stop services
#   Phase 4: Clean local state (no network needed)
#   Phase 5: Docker rebuild (local images, no network needed)
#   Phase 6: Restart services + POST-RESET VERIFICATION
#   Phase 7: WiFi disconnect (LAST — kills SSH if run manually)

# DO NOT use set -e here. A reset script MUST complete ALL phases.
# Individual commands that can fail are guarded with || true.
# If we abort mid-reset, the hub is left in a broken half-reset state:
# BLE won't restart, WiFi won't disconnect, services left in limbo.

ENV_FILE="/root/jupyter-hub-controller/.env"
CONTAINER_ENV_FILE="/root/jupyter-container/.env"
COMPOSE_DIR="/root/jupyter-container"
IDENTITY_BACKUP_DIR="/root/.jupyter-identity-backup"

# Identity vars that MUST be removed during reset (R5: added LOCAL_IP)
IDENTITY_VARS="DEVICE_NAME DEVICE_SECRET HUB_SECRET HUB_BASIC_AUTH HUB_NAME REMOTE_HOST TUNNEL_TOKEN LOCAL_IP END_TASK_BLE CONNECTION_STATUS"

# Disable reset service FIRST to prevent reboot loops (Critical Warning #19)
sudo systemctl disable jupyter-hub-reset.service 2>/dev/null || true

# Signal to identity guard v3 that this reset is INTENTIONAL
# Without this flag, identity guard will attempt to RESTORE identity from backup
touch /tmp/.jupyter-reset-intent

# Remove immutable flag from docker-compose.yml (identity guard v3 sets it)
chattr -i "$COMPOSE_DIR/docker-compose.yml" 2>/dev/null || true

PROGRESS_FILE="/tmp/jupyter-hub-progress.json"
write_progress() {
    local step="$1" total="$2" status="$3" message="$4"
    local percent=$(( step * 100 / total ))
    cat > "$PROGRESS_FILE" <<PEOF
{"operation":"offboard","step":${step},"total_steps":${total},"percent":${percent},"status":"${status}","message":"${message}"}
PEOF
}

write_progress 0 8 "in_progress" "Unlocking system files..."
echo "====== START RESET HUB ======"

# ===============================
# PHASE 0: UNLOCK OTA-PROTECTED FILES
# ===============================
# Reset has absolute authority over OTA lockdown.
# Remove immutable flags so reset can modify any file.
# After reboot, ota-lockdown.sh re-locks everything.
chattr -i -R /usr/local/bin/ /root/jupyter-container/ /opt/ /etc/systemd/system/ /etc/docker/ 2>/dev/null || true
echo "PHASE 0: OTA lockdown removed — all files unlocked for reset"

# ===============================
# PHASE 1: LOAD STATE
# ===============================
write_progress 1 8 "in_progress" "Reading hub configuration..."
# Read env vars safely — strip \r (CRLF), trim whitespace, handle special chars
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        # Trim whitespace and carriage returns
        key=$(echo "$key" | tr -d '\r' | xargs)
        value=$(echo "$value" | tr -d '\r')
        [ -n "$key" ] && export "$key=$value"
    done < "$ENV_FILE"
else
    echo "WARNING: $ENV_FILE not found, skipping environment load."
fi

# ===============================
# PHASE 1.5: RECOVER IDENTITY FROM BACKUPS (if .env was corrupted)
# Without DEVICE_NAME we can't delete the cloud entity — must try all sources
# ===============================
if [ -z "$DEVICE_NAME" ]; then
    echo "DEVICE_NAME missing from .env — checking backup sources..."

    # Source 1: Identity Guard v3 backup
    if [ -f "$IDENTITY_BACKUP_DIR/.env.identity" ]; then
        BACKUP_DEVICE_NAME=$(grep "^DEVICE_NAME=" "$IDENTITY_BACKUP_DIR/.env.identity" 2>/dev/null | cut -d= -f2)
        BACKUP_HUB_SECRET=$(grep "^HUB_SECRET=" "$IDENTITY_BACKUP_DIR/.env.identity" 2>/dev/null | cut -d= -f2)
        BACKUP_DEVICE_SECRET=$(grep "^DEVICE_SECRET=" "$IDENTITY_BACKUP_DIR/.env.identity" 2>/dev/null | cut -d= -f2)
        if [ -n "$BACKUP_DEVICE_NAME" ]; then
            echo "  Found DEVICE_NAME=$BACKUP_DEVICE_NAME in identity guard backup"
            DEVICE_NAME="$BACKUP_DEVICE_NAME"
            [ -z "$HUB_SECRET" ] && [ -n "$BACKUP_HUB_SECRET" ] && HUB_SECRET="$BACKUP_HUB_SECRET"
            [ -z "$DEVICE_SECRET" ] && [ -n "$BACKUP_DEVICE_SECRET" ] && DEVICE_SECRET="$BACKUP_DEVICE_SECRET"
        fi
    fi

    # Source 2: Redundant backup
    if [ -z "$DEVICE_NAME" ] && [ -f "/root/.env.identity.bak" ]; then
        BACKUP_DEVICE_NAME=$(grep "^DEVICE_NAME=" "/root/.env.identity.bak" 2>/dev/null | cut -d= -f2)
        BACKUP_HUB_SECRET=$(grep "^HUB_SECRET=" "/root/.env.identity.bak" 2>/dev/null | cut -d= -f2)
        BACKUP_DEVICE_SECRET=$(grep "^DEVICE_SECRET=" "/root/.env.identity.bak" 2>/dev/null | cut -d= -f2)
        if [ -n "$BACKUP_DEVICE_NAME" ]; then
            echo "  Found DEVICE_NAME=$BACKUP_DEVICE_NAME in redundant backup"
            DEVICE_NAME="$BACKUP_DEVICE_NAME"
            [ -z "$HUB_SECRET" ] && [ -n "$BACKUP_HUB_SECRET" ] && HUB_SECRET="$BACKUP_HUB_SECRET"
            [ -z "$DEVICE_SECRET" ] && [ -n "$BACKUP_DEVICE_SECRET" ] && DEVICE_SECRET="$BACKUP_DEVICE_SECRET"
        fi
    fi

    # Source 3: Argus agent .env (has HUB_NAME which equals DEVICE_NAME)
    if [ -z "$DEVICE_NAME" ] && [ -f "/opt/secureprotect-agent/agent.env" ]; then
        ARGUS_HUB_NAME=$(grep "^HUB_NAME=" "/opt/secureprotect-agent/agent.env" 2>/dev/null | cut -d= -f2)
        if [ -n "$ARGUS_HUB_NAME" ]; then
            echo "  Found DEVICE_NAME=$ARGUS_HUB_NAME in Argus agent .env"
            DEVICE_NAME="$ARGUS_HUB_NAME"
        fi
    fi

    if [ -z "$DEVICE_NAME" ]; then
        echo "  WARNING: Could not recover DEVICE_NAME from ANY source"
    fi
fi

# ===============================
# PHASE 2: NETWORK OPS (need WiFi + credentials)
# Must complete BEFORE credentials are wiped or WiFi disconnected.
# ===============================
write_progress 2 8 "in_progress" "Deregistering from jupyter cloud..."

# --- Cloud delete ---
# Cloud API authenticates with slug_name:hub_secret (Basic Auth)
# DEVICE_NAME = slug_name, HUB_SECRET = hub_secret
CLOUD_API_URL="${JUPYTER_HOST:-https://api.hub.jupyter.com.au}"

if [ -n "$DEVICE_NAME" ]; then
    echo "Deleting hub from cloud database..."
    CLOUD_DELETED=false

    # Primary: use HUB_SECRET (matches cloud's hub_secret field)
    if [ -n "$HUB_SECRET" ]; then
        echo "  Calling DELETE /hub/removed with slug_name:hub_secret..."
        CLOUD_RESPONSE=$(curl -s -w "\n%{http_code}" \
            -X DELETE "${CLOUD_API_URL}/hub/removed" \
            -u "${DEVICE_NAME}:${HUB_SECRET}" \
            --connect-timeout 10 --max-time 30 2>&1) || true
        CLOUD_HTTP=$(echo "$CLOUD_RESPONSE" | tail -1)
        CLOUD_BODY=$(echo "$CLOUD_RESPONSE" | head -n -1)

        if [ "$CLOUD_HTTP" = "200" ] || [ "$CLOUD_HTTP" = "204" ]; then
            echo "  Cloud entity deleted (HTTP $CLOUD_HTTP)"
            CLOUD_DELETED=true
        elif [ "$CLOUD_HTTP" = "404" ]; then
            echo "  Cloud entity not found (HTTP 404) — already clean"
            CLOUD_DELETED=true
        else
            echo "  HUB_SECRET auth failed (HTTP $CLOUD_HTTP): $CLOUD_BODY"
        fi
    fi

    # Fallback: try with DEVICE_SECRET
    if [ "$CLOUD_DELETED" != "true" ] && [ -n "$DEVICE_SECRET" ]; then
        echo "  Trying with DEVICE_SECRET fallback..."
        FALLBACK_RESPONSE=$(curl -s -w "\n%{http_code}" \
            -X DELETE "${CLOUD_API_URL}/hub/removed" \
            -u "${DEVICE_NAME}:${DEVICE_SECRET}" \
            --connect-timeout 10 --max-time 30 2>&1) || true
        FALLBACK_HTTP=$(echo "$FALLBACK_RESPONSE" | tail -1)
        if [ "$FALLBACK_HTTP" = "200" ] || [ "$FALLBACK_HTTP" = "204" ] || [ "$FALLBACK_HTTP" = "404" ]; then
            echo "  Cloud entity deleted via DEVICE_SECRET (HTTP $FALLBACK_HTTP)"
            CLOUD_DELETED=true
        else
            echo "  DEVICE_SECRET auth also failed (HTTP $FALLBACK_HTTP)"
        fi
    fi

    if [ "$CLOUD_DELETED" != "true" ]; then
        echo "ERROR: Could not delete cloud entity for '$DEVICE_NAME'"
        echo "  Manual cleanup needed at: ${CLOUD_API_URL}/admin/hub/hub/"
    fi
else
    echo "WARNING: DEVICE_NAME missing — cannot delete cloud entity"
fi

# --- Hostname/Avahi ---
echo "Resetting hostname and Avahi entries..."
if [ -n "$DEVICE_NAME" ]; then
    sudo sed -i "/jupyter-${DEVICE_NAME}/d" /etc/hosts || true
    sudo sed -i "/jupyter-${DEVICE_NAME}/d" /etc/avahi/hosts 2>/dev/null || true
fi
sudo hostnamectl set-hostname localhost || true
sudo systemctl restart avahi-daemon 2>/dev/null || true

# --- Argus offboarding (with retry) ---
echo "Offboarding from Argus monitoring..."
# R3: machine-id may be empty (cleared during gold image bake). Use DEVICE_NAME as fallback.
DEVICE_ID=""
if [ -s /etc/machine-id ]; then
    DEVICE_ID=$(head -c 12 /etc/machine-id)
fi
if [ -z "$DEVICE_ID" ] && [ -n "$DEVICE_NAME" ]; then
    DEVICE_ID="$DEVICE_NAME"
    echo "  Using DEVICE_NAME as Argus device ID (machine-id empty)"
fi
BRAIN_HOST="argus.jupyterdevices.com"
if [ -f /opt/secureprotect-agent/agent.env ]; then
    BRAIN_FROM_ENV=$(grep '^BRAIN_HOST=' /opt/secureprotect-agent/agent.env 2>/dev/null | cut -d= -f2)
    [ -n "$BRAIN_FROM_ENV" ] && BRAIN_HOST="$BRAIN_FROM_ENV"
fi

ARGUS_OFFBOARDED=false
for attempt in 1 2 3; do
    OFFBOARD_RESPONSE=$(curl -s -X DELETE "http://${BRAIN_HOST}:5053/api/devices/${DEVICE_ID}" \
        --connect-timeout 10 --max-time 30 2>/dev/null || echo '{"error":"timeout"}')
    if echo "$OFFBOARD_RESPONSE" | grep -q '"success".*true'; then
        echo "  Argus offboarding successful (attempt $attempt)"
        ARGUS_OFFBOARDED=true
        break
    elif echo "$OFFBOARD_RESPONSE" | grep -q 'not found'; then
        echo "  Device not enrolled in Argus (already clean)"
        ARGUS_OFFBOARDED=true
        break
    else
        echo "  Argus offboarding attempt $attempt failed: $OFFBOARD_RESPONSE"
        [ "$attempt" -lt 3 ] && sleep 2
    fi
done

if [ "$ARGUS_OFFBOARDED" = "false" ]; then
    echo "  WARNING: Argus offboarding failed after 3 attempts (continuing reset)"
fi

# --- Clean Argus agent identity ---
echo "Cleaning Argus agent identity..."
if [ -f /opt/secureprotect-agent/agent.env ]; then
    sed -i 's/^HUB_NAME=.*/HUB_NAME=/' /opt/secureprotect-agent/agent.env
    echo "  Cleared HUB_NAME from agent.env"
fi

# --- Factory reset all Halo devices ---
# Must happen BEFORE Phase 3 stops services. Django, postgres, HA, and
# transfer_server must all be running for the DELETE endpoint to send
# the factory_reset command to each Halo via the socket relay.
echo "Factory resetting Halo devices..."
HALO_IDS=$(docker exec postgres psql -U postgres -d hub_controller -t -A \
    -c "SELECT id FROM alarm_alarmdevice;" 2>/dev/null) || true

if [ -n "$HALO_IDS" ]; then
    for halo_id in $HALO_IDS; do
        echo "  Sending factory reset to Halo id=$halo_id..."
        HALO_RESPONSE=$(curl -s -w "\n%{http_code}" \
            -X DELETE "http://localhost/api/alarms/${halo_id}" \
            --connect-timeout 10 --max-time 15 2>&1) || true
        HALO_HTTP=$(echo "$HALO_RESPONSE" | tail -1)
        HALO_BODY=$(echo "$HALO_RESPONSE" | head -n -1)
        if [ "$HALO_HTTP" = "200" ] || [ "$HALO_HTTP" = "204" ]; then
            echo "  Halo id=$halo_id factory reset sent + DB record deleted (HTTP $HALO_HTTP)"
        else
            echo "  WARNING: Halo id=$halo_id factory reset failed (HTTP $HALO_HTTP): $HALO_BODY"
        fi
        sleep 2
    done
else
    echo "  No Halo devices registered — skipping"
fi

# ===============================
# PHASE 3: STOP SERVICES
# ===============================
write_progress 3 8 "in_progress" "Shutting down AI engines and cameras..."
echo "Stopping services..."

# Stop identity guard FIRST — prevents it from restoring .env changes
sudo systemctl stop jupyter-identity-guard.timer 2>/dev/null || true
sudo systemctl stop jupyter-identity-guard.service 2>/dev/null || true

sudo systemctl stop secureprotect-agent.service 2>/dev/null || true
if [ -f /opt/secureprotect-agent/docker-compose.yml ]; then
    cd /opt/secureprotect-agent && sudo docker compose down 2>/dev/null || true
    cd /root
fi
# Remove both Argus volumes (agent data + tunnel credentials)
sudo docker volume rm secureprotect-agent_agent_data 2>/dev/null || true
sudo docker volume rm secureprotect-agent_tunnel_data 2>/dev/null || true

# R2: DO NOT use 'cloudflared service uninstall' — it DELETES our portable
# systemd unit (EnvironmentFile-based). Just stop the service and clear the token.
# cloudflared stays alive until Phase 7 — tunnel is the app signal that reset is done
sudo systemctl stop jupyter-hub-controller.service 2>/dev/null || true

# ===============================
# PHASE 4: CLEAN LOCAL STATE (no network needed)
# ===============================
write_progress 4 8 "in_progress" "Erasing all recordings and settings..."

# --- Clear identity vars from ALL .env files ---
echo "Cleaning identity vars from .env files..."
for envfile in "$ENV_FILE" "$CONTAINER_ENV_FILE"; do
    if [ -f "$envfile" ]; then
        for var in $IDENTITY_VARS; do
            sed -i "/^${var}=/d" "$envfile"
        done
        # Set HUB_USER_ID to 0 (setup mode)
        if grep -q "^HUB_USER_ID=" "$envfile"; then
            sed -i 's/^HUB_USER_ID=.*/HUB_USER_ID=0/' "$envfile"
        else
            echo "HUB_USER_ID=0" >> "$envfile"
        fi
        echo "  Cleaned: $envfile"
    fi
done

# --- Clear identity guard backup so it doesn't restore stale vars ---
if [ -d "$IDENTITY_BACKUP_DIR" ]; then
    echo "Cleaning identity guard backup..."
    # Remove .env identity backup
    rm -f "$IDENTITY_BACKUP_DIR/.env.identity" 2>/dev/null || true
    # Also clean any .env backup that has identity vars
    for bak in "$IDENTITY_BACKUP_DIR"/.env*; do
        [ -f "$bak" ] || continue
        for var in $IDENTITY_VARS; do
            sed -i "/^${var}=/d" "$bak" 2>/dev/null || true
        done
        if grep -q "^HUB_USER_ID=" "$bak"; then
            sed -i 's/^HUB_USER_ID=.*/HUB_USER_ID=0/' "$bak"
        fi
        echo "  Cleaned backup: $bak"
    done
fi

# --- Remove credential files ---
for f in /root/jupyter-container/credentials/hub_credentials.json \
         /root/jupyter-container/credentials/iot_credentials.json \
         /root/jupyter-container/credentials/tunnel*.json; do
    [ -f "$f" ] && rm -f "$f" && echo "  Removed: $f"
done

# --- Clean upload folder ---
UPLOAD_DIR="/root/jupyter-hub-controller/upload"
if [ -d "$UPLOAD_DIR" ]; then
    sudo rm -rf "$UPLOAD_DIR"/*
else
    mkdir -p "$UPLOAD_DIR" && sudo chmod a+w "$UPLOAD_DIR"
fi

# --- MediaMTX: strip camera paths ---
echo "Resetting MediaMTX camera paths..."
MEDIAMTX_CONFIG="/root/mediamtx/mediamtx.yml"
if [ -f "$MEDIAMTX_CONFIG" ]; then
    sed -i '/^paths:/,$d' "$MEDIAMTX_CONFIG"
    echo "" >> "$MEDIAMTX_CONFIG"
    echo "paths:" >> "$MEDIAMTX_CONFIG"
    sudo systemctl restart mediamtx 2>/dev/null || true
fi

# --- Ring-MQTT: clean bind-mount data ---
echo "Cleaning Ring-MQTT data..."
rm -rf "$COMPOSE_DIR/ring-mqtt-data/ring-state.json" 2>/dev/null || true
rm -rf "$COMPOSE_DIR/ring-mqtt-data/go2rtc.yaml" 2>/dev/null || true

# --- Frigate: reset config (storage cleanup moved to Phase 5 after docker down) ---
FRIGATE_DIR="$COMPOSE_DIR/frigate"
CONFIG_DIR="$FRIGATE_DIR/config"
SRC_FILE="/root/frigate_config_default.yaml"
DEST_FILE="$CONFIG_DIR/config.yaml"

echo "Resetting Frigate configuration..."
sudo mkdir -p "$CONFIG_DIR"
if [ -f "$SRC_FILE" ]; then
    sudo cp "$SRC_FILE" "$DEST_FILE"
    sudo chmod 644 "$DEST_FILE"
fi
sudo chmod -R 777 "$FRIGATE_DIR"

# --- Home Assistant: FULL RESET ---
echo "Resetting Home Assistant core data..."

HASS_DIR="$COMPOSE_DIR/hass"
HASS_CONFIG_DIR="$HASS_DIR/config"

if [ -d "$HASS_CONFIG_DIR" ]; then
    echo "  Cleaning Home Assistant config..."

    # Remove automation/script/scene
    rm -f "$HASS_CONFIG_DIR/automations.yaml"
    rm -f "$HASS_CONFIG_DIR/scripts.yaml"
    rm -f "$HASS_CONFIG_DIR/scenes.yaml"

    # Recreate empty files (avoid boot crash)
    echo "[]" > "$HASS_CONFIG_DIR/automations.yaml"
    echo "[]" > "$HASS_CONFIG_DIR/scripts.yaml"
    echo "[]" > "$HASS_CONFIG_DIR/scenes.yaml"

    # Remove entity + device registry (CRITICAL reset)
    rm -rf "$HASS_CONFIG_DIR/.storage"

    # Remove database (history, states)
    rm -f "$HASS_CONFIG_DIR/home-assistant_v2.db"
    rm -f "$HASS_CONFIG_DIR/home-assistant_v2.db-*"

    # Optional: remove logs
    rm -f "$HASS_CONFIG_DIR/home-assistant.log"* 2>/dev/null || true

    echo "  Home Assistant core wiped."
else
    echo "  HASS config dir not found, skipping..."
fi


# Clean all user data from PostgreSQL (ensures fresh setup on re-onboard)
# Tables cleared: cameras, zones, settings, Ring account, alarm/Halo devices,
# Meross account/devices, events, faces, vehicles, parcels, garage settings,
# GDrive backup config. Schema and Django internals are preserved.
echo "Cleaning user data tables from hub_controller database..."
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
echo "User data tables cleaned."

# ===============================
# PHASE 5: DOCKER REBUILD (local images, no network needed)
# ===============================
write_progress 5 8 "in_progress" "Restoring to factory settings..."

echo "Stopping ALL Docker containers first..."
cd "$COMPOSE_DIR"
sudo docker compose down --timeout 30 || true

# --- Frigate storage cleanup (AFTER containers are stopped) ---
# Must happen after docker compose down so Frigate isn't holding file locks.
echo "Cleaning Frigate storage (clips, recordings, exports, debugs)..."
FRIGATE_DIR="$COMPOSE_DIR/frigate"
STORAGE_DIR="$FRIGATE_DIR/storage"
for folder in clips exports recordings debugs; do
    TARGET="$STORAGE_DIR/$folder"
    if [ -d "$TARGET" ]; then
        find "$TARGET" -type f -delete 2>/dev/null || true
        find "$TARGET" -mindepth 1 -type d -empty -delete 2>/dev/null || true
        echo "  Cleaned: $TARGET"
    else
        mkdir -p "$TARGET"
    fi
done

# --- Clean user-specific media directories ---
echo "Cleaning user media directories..."
for media_dir in "$COMPOSE_DIR/alarm_audio" "$COMPOSE_DIR/voice_ai_data"; do
    if [ -d "$media_dir" ]; then
        sudo rm -rf "$media_dir"/* 2>/dev/null || true
        echo "  Cleaned: $media_dir"
    fi
done

# --- Generate service secrets BEFORE volume prune ---
# Volume prune destroys postgres data. Fresh postgres needs POSTGRES_PASSWORD
# to initialize. SECRET_KEY is also required for Django to start.
# These are local service secrets, NOT identity -- safe to regenerate.
echo "Generating service secrets for fresh database init..."
DEFAULT_DB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || echo "secureprotect-$(date +%s)")
DEFAULT_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))' 2>/dev/null || echo "django-secret-$(date +%s)")
DEFAULT_SVC_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || echo "svcpass-$(date +%s)")

for envfile in "$ENV_FILE" "$CONTAINER_ENV_FILE"; do
    [ -f "$envfile" ] || continue
    sed -i "s/^DB_PASSWORD=.*/DB_PASSWORD=${DEFAULT_DB_PASSWORD}/" "$envfile"
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${DEFAULT_SECRET_KEY}/" "$envfile"
    for svc_var in MQTT_PASSWORD MQTT_CONTROLLER_PASSWORD MQTT_AI_CONTROLLER_PASSWORD \
                   MQTT_FRIGATE_PASSWORD MQTT_HASS_PASSWORD MQTT_RING_CAMERA_PASSWORD \
                   HASS_PASSWORD FRIGATE_PASSWORD SCRYPTED_PASSWORD; do
        sed -i "s/^${svc_var}=.*/${svc_var}=${DEFAULT_SVC_PASSWORD}/" "$envfile"
    done
    echo "  Service secrets set in: $envfile"
done

# --- Remove Docker volumes (postgres + redis) ---
echo "Removing Docker volumes (postgres + redis)..."
for vol in jupyter-container_postgres-vlm jupyter-container_redis_data; do
    sudo docker volume rm "$vol" 2>/dev/null || echo "  Volume $vol not found or already removed."
done

# iptables: allow Docker containers to reach Django on port 8000
if sudo iptables -L INPUT -n 2>/dev/null | grep -q "DROP.*tcp.*dpt:8000"; then
    sudo iptables -I INPUT 1 -s 172.16.0.0/12 -p tcp --dport 8000 -j ACCEPT 2>/dev/null || true
fi

cd /root

# ===============================
# PHASE 6: RESTART SERVICES + POST-RESET VERIFICATION
# ===============================
write_progress 6 8 "in_progress" "Verifying clean state..."

# R6: Run post-reset verification BEFORE restarting services.
# This ensures identity guard and hub-manager see clean state from the start.
echo "Running post-reset verification..."
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

    # Verify HUB_USER_ID=0
    CURRENT_HUB_ID=$(grep "^HUB_USER_ID=" "$envfile" 2>/dev/null | cut -d= -f2)
    if [ -n "$CURRENT_HUB_ID" ] && [ "$CURRENT_HUB_ID" != "0" ]; then
        echo "  STALE: HUB_USER_ID=$CURRENT_HUB_ID in $envfile — setting to 0"
        sed -i 's/^HUB_USER_ID=.*/HUB_USER_ID=0/' "$envfile"
        VERIFICATION_FAILED=true
    fi
done

if [ "$VERIFICATION_FAILED" = "true" ]; then
    echo "WARNING: Stale identity vars were found and removed during verification"
else
    echo "Verification PASSED — all identity vars clean"
fi

# NOW restart services with verified clean state
echo "Restarting BLE for setup mode..."
sudo systemctl stop jupyter-ble.service 2>/dev/null || true
sudo systemctl enable jupyter-ble.service 2>/dev/null || true
sudo systemctl start jupyter-ble.service 2>/dev/null || true

echo "Ensuring all critical services are enabled + started..."
# for svc in hub-manager.service \
#            jupyter-hub-controller.service \
#            jupyter-hub-celery-camera.service \
#            jupyter-hub-celery-beat.service \
#            jupyter-hub-celery-automation.service \
#            jupyter-hub-celery-restart-ring.service \
#            secureprotect-agent.service \
#            jupyter-identity-guard.timer \
#            haproxy-heal.timer; do
#     sudo systemctl enable "$svc" 2>/dev/null || true
#     sudo systemctl restart "$svc" 2>/dev/null || true
#     echo "  Enabled+Restarted: $svc"
# done
for svc in secureprotect-agent.service \
           jupyter-identity-guard.timer \
           haproxy-heal.timer; do
    sudo systemctl enable "$svc" 2>/dev/null || true
    sudo systemctl restart "$svc" 2>/dev/null || true
    echo "  Enabled+Restarted: $svc"
done

# ===============================

# ===============================
# PHASE 7: KILL TUNNEL (signal to app that reset is done)
# Cloudflare tunnel is kept alive through Phases 3-6 so the app can
# poll /resetting/progress the entire time. Killing it HERE tells the
# app: offboard is complete, hub is about to reboot.
# The app then watches for BLE advertising as the ready signal.
# ===============================
write_progress 7 8 "in_progress" "Preparing hub for new owner..."
echo "Stopping Cloudflare tunnel (reset signal to app)..."
sudo systemctl stop cloudflared.service 2>/dev/null || true
echo "  Tunnel stopped — app will detect disconnect"
# Brief pause so the app has time to detect tunnel loss before reboot
sleep 3

# PHASE 8: WIFI DISCONNECT (LAST — kills SSH if run manually)
# ===============================
write_progress 8 8 "complete" "Reset complete! Hub is rebooting into setup mode."
echo "Disconnecting WiFi..."
# R4: WiFi interface is wlP2p33s0 on Rock5B+, NOT wlan0. Detect dynamically.
WIFI_IFACE=$(nmcli -t -f DEVICE,TYPE dev | grep ':wifi$' | head -1 | cut -d: -f1)
if [ -n "$WIFI_IFACE" ]; then
    sudo nmcli dev disconnect "$WIFI_IFACE" 2>/dev/null || true
    echo "  Disconnected WiFi interface: $WIFI_IFACE"
else
    echo "  No WiFi interface found to disconnect"
fi
for conn in $(nmcli -t -f NAME,TYPE connection show | grep wireless | cut -d: -f1); do
    sudo nmcli connection delete "$conn" 2>/dev/null || true
    echo "  Deleted WiFi connection: $conn"
done

# Remove physical NM profile files from disk (nmcli delete only removes in-memory)
echo "Removing NetworkManager profile files from disk..."
NM_PROFILES="/etc/NetworkManager/system-connections"
if [ -d "$NM_PROFILES" ]; then
    PROFILE_COUNT=$(ls -1 "$NM_PROFILES"/ 2>/dev/null | wc -l)
    rm -f "$NM_PROFILES"/*.nmconnection 2>/dev/null || true
    rm -f "$NM_PROFILES"/* 2>/dev/null || true
    echo "  Removed $PROFILE_COUNT profile files"
fi

# Clear NM internal state: BSSID cache, device history, DHCP leases (CW#197)
echo "Clearing NetworkManager internal state..."
rm -rf /var/lib/NetworkManager/internal-* 2>/dev/null || true
rm -rf /var/lib/NetworkManager/seen-bssids 2>/dev/null || true
rm -rf /var/lib/NetworkManager/timestamps 2>/dev/null || true
rm -f /var/lib/NetworkManager/NetworkManager-intern.conf 2>/dev/null || true
rm -f /var/lib/NetworkManager/NetworkManager.state 2>/dev/null || true
rm -f /var/lib/dhcp/dhclient*.leases 2>/dev/null || true
rm -f /var/lib/dhclient/dhclient*.leases 2>/dev/null || true
rm -f /var/lib/NetworkManager/dhclient*.conf 2>/dev/null || true
rm -f /var/lib/NetworkManager/*.lease 2>/dev/null || true
rm -f /etc/wpa_supplicant/wpa_supplicant*.conf 2>/dev/null || true
echo "  NM state + DHCP leases cleared"

# Clean BLE credential caches
rm -f /root/jupyter-ble-controller/wifi_credentials.json 2>/dev/null || true
rm -f /root/jupyter-ble-controller/iot_credentials.json 2>/dev/null || true
rm -f /root/jupyter-ble-controller-a/wifi_credentials.json 2>/dev/null || true
rm -f /root/jupyter-ble-controller-a/iot_credentials.json 2>/dev/null || true
echo "  BLE credential caches cleared"

echo "====== RESET COMPLETE ======"
# Reset service already disabled at top of script (moved from here for safety)
echo "Rebooting hub for clean state..."
sudo reboot
