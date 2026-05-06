# TURN credential rotation runbook

**Status:** runbook only — current TURN credentials are static fleet-wide. Per-hub HMAC TURN is a 2-week design + impl deferred to post-pilot.

**Date:** 2026-05-07

## Current state

- TURN server: `turn:ap-southeast-2.coturn.dev.jupyter.com.au:3478`
- Username: `ap-southeast-2-dev-jupyter` (shared, fleet-wide)
- Password: `Z08242522ZBQHX263VIJ1` (shared, fleet-wide static secret)
- Configured at: MediaMTX `webrtcICEServers2:` block on every hub

**Risk:** if any one hub is compromised, the TURN secret leaks for the entire fleet. No automatic rotation. Customers' WebRTC video sessions continue to relay via this single shared cred.

## When to rotate (triggers)

Rotate within **24 hours** if any of these:

1. **Suspected hub compromise** — physical theft, customer-reported anomalous remote access, our internal alerting flags suspicious patterns.
2. **Credential leaked outside the trusted tooling chain** — accidental commit to a public repo, screenshot in a public ticket, etc.
3. **Cloud-side TURN provider rotation** — coturn upstream, or our own infra change.

Rotate within **30 days** if:

4. **Routine hygiene** — quarterly rotation as defence-in-depth, since the static cred has been in use since the project began.

## Rotation procedure (manual today, automated post-pilot)

### Step 1 — generate new credentials cloud-side

On the coturn server (or wherever the TURN realm is configured):

```
turnadmin -k -u ap-southeast-2-dev-jupyter -r ap-southeast-2.coturn.dev.jupyter.com.au -p <NEW_PASSWORD>
```

Or whatever the equivalent command is for your TURN provider. New password must be ≥20 chars random.

### Step 2 — push new credentials to every hub via Argus

Argus is the only authorised writer (per `feedback-no-otherwise-ota.md`). Use the `/api/groups/<id>/script` endpoint.

```bash
# script body — runs on every hub in the group
NEW_PASSWORD="<NEW_PASSWORD>"
chattr -i /root/mediamtx/mediamtx.yml 2>/dev/null || true
sed -i "s/password: .*/password: $NEW_PASSWORD/" /root/mediamtx/mediamtx.yml
chattr +i /root/mediamtx/mediamtx.yml 2>/dev/null || true
docker compose -f /root/jupyter-container/docker-compose.yml restart mediamtx
```

**Caveat:** mediamtx.yml is a generated file (rendered by Celery from `camera/templates/`). The TURN credentials must also be updated in the **template** at `/root/jupyter-hub-controller/camera/templates/mediamtx.yml`, otherwise the next render will overwrite the patched value. See CW#172.

### Step 3 — update template fleet-wide

Same Argus push, but for the hub-controller template:

```bash
TPL=/root/jupyter-hub-controller/camera/templates/mediamtx.yml
sed -i "s/password: .*/password: $NEW_PASSWORD/" "$TPL"
# trigger Celery render to apply to mediamtx.yml
cd /root/jupyter-hub-controller && source .venv/bin/activate && \
  python manage.py shell -c "from camera.tasks import render_mediamtx_config; render_mediamtx_config.delay()"
```

### Step 4 — update cloud-side defaults

Wherever the cloud onboarding service reads TURN creds for new hubs (likely `jupyter-backend/apps/hub/settings.py` or env), update there too. Future onboards get the new creds.

### Step 5 — verify

For each hub group, after Argus push:

```bash
# from Argus brain or from a hub:
curl -s http://localhost:9997/v3/paths/list | python3 -m json.tool | grep -A2 webrtc
# check live WebRTC session via the app on a known camera
```

Successful WebRTC session = creds rotated cleanly. Fall back: roll back via Argus push of old password.

## What to do post-pilot (replace this runbook with code)

1. **Per-hub credentials** — at onboard, cloud mints a per-hub TURN credential pair, returns via `/hub/credential` payload alongside `iot_credentials.json`. Stored on hub via BLE just like the existing identity vars (CW#21 atomic write).
2. **Time-limited HMAC** — RFC 8489 short-term TURN credentials. The TURN server validates a HMAC-SHA1 of `<expiry>:<username>` using a shared HMAC key. Hub gets a fresh HMAC every N hours from the cloud.
3. **Rotation cadence** — 90-day full rotation (cred + HMAC key cloud-side); 1-hour HMAC refresh per session. Argus pushes the new HMAC key as part of routine rotation; hubs accept new key at next CF tunnel reconnect.

Estimated implementation: 2 weeks (cloud + hub + iOS). See `gold-image-readiness-2026-05-07.md` for full TURN per-hub design proposal once drafted.

## Related

- `~/.claude/projects/-Users-topsycombs/memory/critical-warnings.md` — CW#172 (template-as-source-of-truth)
- `~/.claude/projects/-Users-topsycombs/memory/feedback-no-otherwise-ota.md` — Argus is the only authorised OTA writer
