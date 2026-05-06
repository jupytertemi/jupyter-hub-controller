#!/bin/bash
# audio-pipeline-watchdog.sh — Lightweight audio pipeline health monitor
#
# Checks: Halo → audio_server → VoiceAI → HA chain
# Runs every 60s via systemd timer. Restarts broken services.
#
# Failure modes handled:
#   1. audio_server container down → restart it
#   2. Halo not connected (no sender) → MQTT restart command
#   3. VoiceAI container down → restart it
#   4. VoiceAI listener thread dead → restart container
#   5. SoundAI container down → restart it
#
# Keeps it simple — no state files, no dependencies, just docker.

LOG_TAG="audio-watchdog"

log() { logger -t "$LOG_TAG" "$1"; }

# ── 1. Check audio_server container is running ──
if ! docker inspect -f '{{.State.Running}}' audio_server 2>/dev/null | grep -q true; then
    log "HEAL: audio_server not running, restarting"
    docker restart audio_server 2>/dev/null || true
    sleep 5
    exit 0  # Let next cycle check downstream
fi

# ── 2. Check audio_server health (inside container) ──
HEALTH=$(docker exec audio_server python3 -c "
import urllib.request, json, sys
try:
    r = urllib.request.urlopen('http://localhost:5556/health', timeout=3)
    print(r.read().decode())
except: print('{}')
" 2>/dev/null || echo '{}')

SENDER_CONNECTED=$(echo "$HEALTH" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print('true' if d.get('sender', {}).get('connected') else 'false')
except: print('false')
" 2>/dev/null)

RECEIVERS=$(echo "$HEALTH" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('receivers', 0))
except: print(0)
" 2>/dev/null)

# ── 3. If no sender (Halo disconnected), send MQTT restart ──
if [ "$SENDER_CONNECTED" = "false" ]; then
    # Find Halo device identity from Django DB
    IDENTITY=$(docker exec postgres psql -U postgres -d hub_controller -tAc \
        "SELECT identity_name FROM alarm_alarmdevice LIMIT 1;" 2>/dev/null | tr -d '[:space:]')

    if [ -n "$IDENTITY" ] && [ "$IDENTITY" != "" ]; then
        MAC_SUFFIX="${IDENTITY##*-}"
        MQTT_TOPIC="jupyter-alarm-${MAC_SUFFIX}/button/restart/command"
        log "HEAL: No sender (Halo disconnected). Sending MQTT restart to $MQTT_TOPIC"
        /root/jupyter-hub-controller/.venv/bin/python3 -c "
import paho.mqtt.client as mqtt
import time
c = mqtt.Client()
try:
    c.username_pw_set('django', 'django')
    c.connect('localhost', 1883, 5)
    c.publish('$MQTT_TOPIC', 'PRESS', qos=0)
    time.sleep(0.5)
    c.disconnect()
except: pass
" 2>/dev/null || true
    fi
fi

# ── 4. Check VoiceAI container ──
if ! docker inspect -f '{{.State.Running}}' jupyter_voice_ai 2>/dev/null | grep -q true; then
    log "HEAL: jupyter_voice_ai not running, restarting"
    docker restart jupyter_voice_ai 2>/dev/null || true
    sleep 5
    exit 0
fi

# ── 5. Check VoiceAI health (thread liveness) ──
VOICE_HEALTH=$(docker exec jupyter_voice_ai python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen('http://localhost:8002/health', timeout=3)
    print(r.read().decode())
except: print('{}')
" 2>/dev/null || echo '{}')

THREAD_ALIVE=$(echo "$VOICE_HEALTH" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    al = d.get('audio_listener', {})
    print('true' if al and al != 'not_started' else 'false')
except: print('false')
" 2>/dev/null)

if [ "$THREAD_ALIVE" = "false" ]; then
    log "HEAL: VoiceAI listener thread dead, restarting container"
    docker restart jupyter_voice_ai 2>/dev/null || true
fi

# ── 6. Check SoundAI container ──
if ! docker inspect -f '{{.State.Running}}' sound_detection 2>/dev/null | grep -q true; then
    log "HEAL: sound_detection not running, restarting"
    docker restart sound_detection 2>/dev/null || true
fi

# ── 7. Warn on low receiver count ──
if [ "$RECEIVERS" -lt 2 ] 2>/dev/null && [ "$SENDER_CONNECTED" = "true" ]; then
    log "WARN: Only $RECEIVERS receivers connected (expected 2: VoiceAI + SoundAI)"
fi
