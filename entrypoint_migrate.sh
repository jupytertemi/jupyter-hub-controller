#!/bin/bash

ENV_FILE="/root/jupyter-hub-controller/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# === AUTO-GENERATE SECRETS IF EMPTY ===
# Gold image sanitization blanks these. On first boot after flash, generate new ones.
generate_secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe($1))"
}

SECRETS_GENERATED=false
SECRET_VARS=(
    DB_PASSWORD
    SECRET_KEY
    MQTT_PASSWORD
    MQTT_CONTROLLER_PASSWORD
    MQTT_AI_CONTROLLER_PASSWORD
    MQTT_FRIGATE_PASSWORD
    MQTT_HASS_PASSWORD
    MQTT_RING_CAMERA_PASSWORD
    HASS_PASSWORD
    FRIGATE_PASSWORD
    SCRYPTED_PASSWORD
)

for var in "${SECRET_VARS[@]}"; do
    val="${!var:-}"
    if [ -z "$val" ]; then
        new_val=$(generate_secret 24)
        sed -i "s|^${var}=.*|${var}=${new_val}|" "$ENV_FILE"
        export "$var=$new_val"
        echo "Generated: $var"
        SECRETS_GENERATED=true
    fi
done

# Always sync POSTGRES_PASSWORD to Docker .env (fixes re-onboard where DB_PASSWORD
# survives offboard but POSTGRES_PASSWORD doesn't get synced — CW#23)
DB_PASSWORD="${DB_PASSWORD:-$(grep '^DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)}"
DOCKER_ENV="/root/jupyter-container/.env"
if [ -f "$DOCKER_ENV" ]; then
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${DB_PASSWORD}|" "$DOCKER_ENV"
fi

if [ "$SECRETS_GENERATED" = true ]; then
    echo "=== Secrets generated and written to .env ==="
fi

# Re-export with current values (picks up any newly generated secrets)
export $(grep -v '^#' "$ENV_FILE" | xargs)

cd /root/jupyter-container

STOP_TIMEOUT=30
START_WAIT=5

echo "=== Starting core services ==="
docker compose up -d emqx
sleep ${START_WAIT}

docker compose up -d emqx_setting
sleep ${START_WAIT}

docker compose up -d postgres redis jupyter_homeassistant hass_setting
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

echo "=== Syncing Postgres password (CW#23) ==="
docker exec postgres psql -U postgres -c "ALTER USER postgres WITH PASSWORD '${DB_PASSWORD}';" 2>/dev/null || true

echo "=== Syncing Home Assistant password (CW#251) ==="
# Same class of bug as CW#23: HASS_PASSWORD in .env may not match HA's stored password
# (e.g. after re-onboard, secret rotation, or hass_setting crash-loop on already-onboarded HA)
HA_CONTAINER="jupyter_homeassistant"
if docker ps --format '{{.Names}}' | grep -q "^${HA_CONTAINER}$"; then
    # Wait for HA to be ready (max 30s)
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8123/api/ >/dev/null 2>&1; then break; fi
        sleep 1
    done
    docker exec "${HA_CONTAINER}" hass --script auth -c /config change_password root "${HASS_PASSWORD}" 2>/dev/null \
        && echo "HA password synced" \
        || echo "WARNING: HA password sync failed (non-fatal, may need restart)"
fi

echo "=== Creating databases if missing (needed after hard reset volume prune) ==="
docker exec postgres psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='hub_controller'" 2>/dev/null | grep -q 1 || {
    docker exec postgres psql -U postgres -c "CREATE DATABASE hub_controller;" 2>/dev/null
    echo "Created hub_controller database"
}
docker exec postgres psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='events'" 2>/dev/null | grep -q 1 || {
    docker exec postgres psql -U postgres -c "CREATE DATABASE events;" 2>/dev/null
    echo "Created events database"
}

echo "=== Installing pgvector extension (required for HNSW indexes) ==="
docker exec postgres psql -U postgres -d hub_controller -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true

echo "=== Applying database migrations ==="
if ! python manage.py migrate --noinput; then
    echo "ERROR: Django migrations FAILED. Retrying once after 5s..."
    sleep 5
    if ! python manage.py migrate --noinput; then
        echo "CRITICAL: Django migrations failed TWICE. Hub database is incomplete."
        echo "  Manual intervention required: run 'python manage.py migrate' from venv"
        exit 1
    fi
fi
echo "=== All migrations applied successfully ==="

echo "=== Starting ALL Docker containers ==="
cd /root/jupyter-container
docker compose up -d 2>&1 || echo "WARNING: docker compose up had errors (some containers may need manual start)"
cd /root/jupyter-hub-controller

echo "Collecting static files..."
python manage.py collectstatic --noinput
echo "=== Refreshing JWKS public key ==="
python refresh_pubkey.py || echo "WARNING: refresh_pubkey failed (non-fatal)"

echo "=== Ensuring hub user exists ==="
python ensure_hub_user.py || echo "WARNING: ensure_hub_user failed (non-fatal)"

echo "=== Seeding garage automation defaults (idempotent) ==="
# Creates GarageDoorSettings + HA automations IFF exactly one MerossDevice and one
# Camera exist on this hub. Multi-device hubs skip automatically and are configured
# via the app UI. No-op when a row already exists. Non-fatal on any error so the
# entrypoint never blocks boot on a missing Meross device.
python manage.py populate_garage_settings 2>&1 || echo "WARNING: populate_garage_settings failed (non-fatal — configure via app)"
