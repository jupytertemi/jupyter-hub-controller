#!/bin/bash
set -e

ENV_FILE="/root/jupyter-hub-controller/.env"
DST_ENV="/root/jupyter-container/.env"

echo "=== Syncing .env file ==="

if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$DST_ENV"
  echo "✅ Copied .env to jupyter-container"
else
  echo "⏳ Source .env not found yet: $ENV_FILE"
fi

# Auto-detect timezone from host and inject into .env for Docker containers.
# Containers with TZ baked into their image would otherwise ignore /etc/timezone bind-mount.
# The host timezone is set during onboarding from the phone's locale.
HOST_TZ=$(cat /etc/timezone 2>/dev/null || echo "UTC")
if [ -f "$DST_ENV" ]; then
  sed -i '/^TZ=/d' "$DST_ENV"
  echo "TZ=${HOST_TZ}" >> "$DST_ENV"
  echo "✅ Timezone set to ${HOST_TZ} in .env"
fi

systemctl start docker

# S2: Split required vars into always-required and onboarded-only.
# On fresh gold image (HUB_USER_ID=0), HUB_BASIC_AUTH is not set yet.
# BLE onboard sets it, then restarts this service. Django must start
# in setup mode so BLE health_check can succeed.
ALWAYS_REQUIRED=(
  HUB_USER_ID
  DB_HOST
  DB_PORT
)

MAX_RETRIES=30
SLEEP_TIME=2
COUNT=0

echo "=== Waiting for ENV file & variables ==="

while true; do
  if [ -f "$ENV_FILE" ]; then

    for var in "${ALWAYS_REQUIRED[@]}"; do
      unset $var
    done

    set -o allexport
    source "$ENV_FILE"
    set +o allexport

    MISSING=0

    for var in "${ALWAYS_REQUIRED[@]}"; do
      if [ -z "${!var}" ]; then
        echo "Missing $var..."
        MISSING=1
      fi
    done

    if [ "$MISSING" -eq 0 ]; then
      echo "All required ENV loaded (HUB_USER_ID=$HUB_USER_ID)"
      break
    fi
  else
    echo "ENV file not found: $ENV_FILE"
  fi

  COUNT=$((COUNT+1))
  if [ "$COUNT" -ge "$MAX_RETRIES" ]; then
    echo "Timeout waiting for ENV variables"
    exit 1
  fi

  sleep "$SLEEP_TIME"
done


cd /root/jupyter-container

STOP_TIMEOUT=30
START_WAIT=5

echo "=== Stopping main services (graceful ${STOP_TIMEOUT}s) ==="
docker compose down \
  --timeout ${STOP_TIMEOUT} \
  haproxy-service event_listener face_training jupyter_voice_ai sound_detection jupyter_homeassistant || true

echo "=== Ensuring containers are stopped ==="
for c in haproxy-service event_listener face_training jupyter_voice_ai sound_detection jupyter_homeassistant; do
  if docker ps -q -f name=$c | grep -q .; then
    echo "⚠️ $c still running, force killing"
    docker kill $c || true
    docker rm -f $c || true
  fi
done

echo "=== Waiting for network & DNS ==="
until ping -c1 8.8.8.8 >/dev/null 2>&1; do sleep 2; done
# S1: Use JUPYTER_HOST from .env (production URL), NOT hardcoded dev domain
DNS_HOST=$(echo "${JUPYTER_HOST:-https://api.hub.jupyter.com.au}" | sed 's|https\?://||')
until getent hosts "$DNS_HOST" >/dev/null 2>&1; do sleep 2; done

# S3: Argus monitoring onboard — update hub identity in agent config and restart.
# Agent binaries are pre-installed on gold image. Only identity needs updating.
ARGUS_DIR="/opt/secureprotect"
if [ -d "$ARGUS_DIR/bin" ] && [ -n "$DEVICE_NAME" ]; then
  echo "=== Onboarding Argus monitoring agent ==="

  # Update scrape config with hub identity
  if [ -f "$ARGUS_DIR/config/scrape.yml" ]; then
    sed -i "s/location: .*/location: ${DEVICE_NAME}/" "$ARGUS_DIR/config/scrape.yml"
    sed -i "s/hub_name: .*/hub_name: '${DEVICE_NAME}'/" "$ARGUS_DIR/config/scrape.yml"
    echo "  Updated scrape.yml"
  fi

  # Update agent metadata
  if [ -f "$ARGUS_DIR/config/agent.json" ]; then
    sed -i "s/\"hub_name\":\"[^\"]*\"/\"hub_name\":\"${DEVICE_NAME}\"/" "$ARGUS_DIR/config/agent.json"
    echo "  Updated agent.json"
  fi

  # Update systemd service files with new hub name
  VMAGENT_SVC="/etc/systemd/system/secureprotect-vmagent.service"
  if [ -f "$VMAGENT_SVC" ]; then
    sed -i "s/-remoteWrite.label=location=[^ ]*/-remoteWrite.label=location=${DEVICE_NAME}/" "$VMAGENT_SVC"
    echo "  Updated vmagent service"
  fi

  IP_SVC="/etc/systemd/system/secureprotect-ip-reporter.service"
  if [ -f "$IP_SVC" ]; then
    sed -i "s/^Environment=HUB_NAME=.*/Environment=HUB_NAME=${DEVICE_NAME}/" "$IP_SVC"
    echo "  Updated ip-reporter service"
  fi

  # Update report script fallback
  if [ -f "$ARGUS_DIR/bin/report-public-ip.sh" ]; then
    sed -i "s/__HUB_NAME_FALLBACK__/${DEVICE_NAME}/; s/HUB_NAME:-[^}]*/HUB_NAME:-${DEVICE_NAME}/" "$ARGUS_DIR/bin/report-public-ip.sh" 2>/dev/null || true
  fi

  # Also update Docker agent identity if present
  if [ -f /opt/secureprotect-agent/agent.env ]; then
    sed -i "s/^HUB_NAME=.*/HUB_NAME=${DEVICE_NAME}/" /opt/secureprotect-agent/agent.env
    echo "  Updated Docker agent identity"
  fi

  systemctl daemon-reload
  systemctl restart secureprotect-node-exporter secureprotect-vmagent 2>/dev/null || true
  systemctl restart secureprotect-ip-reporter 2>/dev/null || true
  echo "  Argus agent onboarded as: ${DEVICE_NAME}"
else
  [ ! -d "$ARGUS_DIR/bin" ] && echo "=== Argus agent not installed, skipping ==="
  [ -z "$DEVICE_NAME" ] && echo "=== DEVICE_NAME not set, skipping Argus onboard ==="
fi

echo "=== Starting application services ==="
docker compose up -d event_listener face_training jupyter_voice_ai sound_detection jupyter_homeassistant face_recognition transfer_server audio_server ring-webrtc ring_mqtt video_server parcel_detection number_plate_detection loiter_detection suggested_faces clip_transcoder node-exporter
sleep ${START_WAIT}

docker compose up -d hass_setting
sleep ${START_WAIT}

echo "=== Starting HAProxy ==="
docker compose up -d haproxy-service
sleep ${START_WAIT}

cd ..

cd /root/jupyter-hub-controller
source .venv/bin/activate
chmod +x ./entrypoint.sh

echo "=== Waiting for Postgres to accept connections ==="
while ! nc -z ${DB_HOST} ${DB_PORT}; do
  echo "Waiting for Postgres Database Startup"
  sleep 1
done

exec ./entrypoint.sh
