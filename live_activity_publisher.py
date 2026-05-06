#!/usr/bin/env python3
"""
Live Activity + General Notification Publisher (hub-direct, all paths)

Subscribes to local EMQX:
  /events     AI events (CAR/PERSON/PARCEL/AUDIO)
              → APNs Live Activity push-to-start (LiveActivityWidgetAttributes)
              → APNs alert push (regular notification, hub-direct)
              → FCM general notification (fallback / Android)

  +/status    Halo telemetry → APNs Halo Live Activity (HaloChargingActivityAttributes)
                              + APNs alert + FCM

Bypasses the broken cloud→SQS→Django pipeline entirely.

Env (from /root/jupyter-hub-controller/.env):
  APNS_BUNDLE_ID, APNS_TEAM_ID, APNS_KEY_ID, APNS_PRIVATE_KEY_PATH
  LIVE_ACTIVITY_START_TOKEN     — per-owner LA token, refreshed every 10 min
  HALO_CHARGING_START_TOKEN     — per-owner Halo LA token
  FCM_REGISTRATION_IDS          — comma-sep list of owner's FCM device tokens
  APNS_DEVICE_TOKENS            — comma-sep list of owner's RAW APNs tokens (Plan B)
  FIREBASE_CRED_PATH            — service-account JSON for FCM HTTP v1 send
"""

import json
import logging
import os
import sys
import threading
import time
from threading import Lock

import httpx
import jwt
import paho.mqtt.client as mqtt

import firebase_admin
from firebase_admin import credentials, messaging

ENV_PATH = "/root/jupyter-hub-controller/.env"


def _load_env(path=ENV_PATH, force=False):
    """Read .env into os.environ. Default behaviour: setdefault (don't clobber
    already-set vars). With force=True: overwrite — used by the periodic token
    refresh below so newly-registered device tokens get picked up without a
    publisher restart.
    """
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if force:
                os.environ[k] = v
            else:
                os.environ.setdefault(k, v)


_load_env()

BUNDLE_ID = os.environ["APNS_BUNDLE_ID"]
TEAM_ID = os.environ["APNS_TEAM_ID"]
KEY_ID = os.environ["APNS_KEY_ID"]
KEY_PATH = os.environ["APNS_PRIVATE_KEY_PATH"]

LA_TOKEN = os.environ.get("LIVE_ACTIVITY_START_TOKEN", "")
HALO_TOKEN = os.environ.get("HALO_CHARGING_START_TOKEN", "")
FCM_TOKENS = [t for t in os.environ.get("FCM_REGISTRATION_IDS", "").split(",") if t]
APNS_RAW_TOKENS = [t for t in os.environ.get("APNS_DEVICE_TOKENS", "").split(",") if t]
FIREBASE_CRED_PATH = os.environ.get("FIREBASE_CRED_PATH", "")

# APNs has two endpoints. Tokens minted by debug-build apps work ONLY against
# sandbox; tokens minted by TestFlight/App Store builds work ONLY against
# production. The token itself doesn't tell you which — the build does — so
# every token in our store carries an "environment" field, and we route each
# push accordingly. Default below covers Live Activity tokens whose
# environment was never declared (treated as production for backwards-compat).
APNS_BASE_PRODUCTION = "https://api.push.apple.com"
APNS_BASE_SANDBOX = "https://api.sandbox.push.apple.com"
LIVE_ACTIVITY_ENVIRONMENT = os.environ.get("LIVE_ACTIVITY_ENVIRONMENT", "production")


def _apns_base_url(environment):
    """Return the APNs endpoint base URL for a given environment string.
    Unknown values fall back to production so we never silently drop pushes;
    if a customer hub's env vars are misconfigured, the worst case is wrong-
    endpoint failures (Apple returns BadDeviceToken → token is auto-cleaned)
    rather than the publisher crashing."""
    return APNS_BASE_SANDBOX if environment == "sandbox" else APNS_BASE_PRODUCTION


THROTTLE_SECONDS = 15
# Per-named-person, per-camera, per-notification_type cooldown — suppresses
# floods of "Kevin spotted" when the same recognised person walks past the
# same camera repeatedly inside the window. Different camera, different
# notification_type, or different person → fires normally. Loitering is a
# distinct notification_type so it's never suppressed by spotting events
# (and vice versa). See feedback memory: notification cooldown design 2026-05-06.
PERSON_COOLDOWN_SECONDS = int(os.getenv("PERSON_COOLDOWN_SECONDS", "60"))
# Looser cooldown for unknown persons since we can't dedup by person_id.
# Tightens the existing label-throttle for PERSON+unknown to 60s instead
# of the global 15s. Loses some detail; massively cuts noise during a
# stranger lingering near a door.
UNKNOWN_PERSON_COOLDOWN_SECONDS = int(os.getenv("UNKNOWN_PERSON_COOLDOWN_SECONDS", "60"))
SUPPORTED_LABELS = {"AUDIO", "PARCEL", "PERSON", "CAR", "LOITERING"}

# Notification types that fire a banner-only push (no Live Activity card).
# Live Activity cards persist on the lock screen with action buttons (e.g.
# the Open/Close Garage button on garage_detected widgets) and should only
# fire for actionable events. Non-actionable notification_types listed here
# get the alert banner but skip push_la_ai_event entirely. Easy to extend
# without touching _handle_ai_event control flow.
LA_SKIP_TYPES = {"vehicle_spotted"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("la-publisher")

with open(KEY_PATH) as f:
    PRIVATE_KEY = f.read()

_firebase_app = None
if FIREBASE_CRED_PATH and os.path.exists(FIREBASE_CRED_PATH):
    try:
        _firebase_app = firebase_admin.initialize_app(credentials.Certificate(FIREBASE_CRED_PATH))
        log.info("Firebase Admin initialised")
    except Exception as ex:
        log.exception("Firebase Admin init failed: %s", ex)

_halo_charge_state = {}
_halo_lock = Lock()
_throttle_lock = Lock()
_last_event = {}
_last_label = {}
# (person_id, camera_slug, notification_type) -> ts of last fire
_last_person = {}
# (camera_slug,) -> ts of last unknown-PERSON fire (replaces label-throttle
# scope for unknown persons; per-camera so different rooms still fire)
_last_unknown_per_cam = {}


def _throttle_ok(event_id, label):
    now = time.time()
    with _throttle_lock:
        if now - _last_label.get(label, 0) < THROTTLE_SECONDS:
            return False, f"label-throttle {label}"
        if event_id and now - _last_event.get(event_id, 0) < THROTTLE_SECONDS:
            return False, f"event-throttle {event_id}"
        if event_id:
            _last_event[event_id] = now
        _last_label[label] = now


def _person_cooldown_ok(notification_type, msg):
    """Per-named-person + per-camera + per-notification_type cooldown gate.

    Stacks BELOW the existing event_id throttle. Catches the case where the
    same recognised person triggers multiple distinct Frigate events on the
    same camera within the window (Kevin walks past Front Door 3x in 30s).

    For unknown persons (no person_id, or sub_label is "Unknown"/"someone"),
    falls back to a per-camera tightened cooldown so a stranger lingering
    doesn't fire every 15s.

    Returns (True, "ok") to allow the notification, (False, reason) to
    suppress it with a logged reason.
    """
    if msg.get("label") != "PERSON" and notification_type != "loitering_detected":
        # only gate person-flavoured notifications; vehicle/parcel/audio paths
        # use their own throttling upstream
        return True, "ok"
    person_id = msg.get("person_id")
    sub_label = (msg.get("sub_label") or "").strip().lower()
    camera = msg.get("camera_name") or "*"
    is_unknown = (not person_id) or sub_label in ("", "unknown", "someone")
    now = time.time()
    with _throttle_lock:
        # lazy eviction — drop entries older than 5x the cooldown window so
        # the maps don't grow unbounded on long-running publishers
        evict_before = now - PERSON_COOLDOWN_SECONDS * 5
        for k in [k for k, v in _last_person.items() if v < evict_before]:
            _last_person.pop(k, None)
        for k in [k for k, v in _last_unknown_per_cam.items() if v < evict_before]:
            _last_unknown_per_cam.pop(k, None)
        if is_unknown:
            key = (camera,)
            last = _last_unknown_per_cam.get(key, 0)
            if now - last < UNKNOWN_PERSON_COOLDOWN_SECONDS:
                return False, (f"unknown-cooldown cam={camera} "
                               f"elapsed={now-last:.0f}s "
                               f"limit={UNKNOWN_PERSON_COOLDOWN_SECONDS}s")
            _last_unknown_per_cam[key] = now
            return True, "ok"
        # named person
        key = (person_id, camera, notification_type)
        last = _last_person.get(key, 0)
        if now - last < PERSON_COOLDOWN_SECONDS:
            return False, (f"person-cooldown person_id={person_id} cam={camera} "
                           f"type={notification_type} elapsed={now-last:.0f}s "
                           f"limit={PERSON_COOLDOWN_SECONDS}s")
        _last_person[key] = now
        return True, "ok"
    return True, "ok"


# ---------- Classifier ----------

def classify_ai_event(msg):
    label = msg.get("label")
    if label not in SUPPORTED_LABELS:
        return None, None, None

    if label == "AUDIO":
        return "unusual_sound_detected", "Unusual Sound Detected", \
               f"{msg.get('camera_name','your camera')} heard something"

    if label == "PARCEL":
        ps = msg.get("parcel_status") or ""
        person_id = msg.get("person_id")
        if ps in ("parcel_theft_attempt", "parcel_theft_warning") or (ps == "picked_up" and not person_id):
            return "parcel_theft_detected", "Parcel pickup by unknown person", \
                   "Someone unknown is picking up your parcel"
        if ps in ("picked_up", "parcel_pickup") and person_id:
            who = msg.get("sub_label") or "someone"
            return "parcel_theft_detected", f"Parcel collected by {who}", f"{who} picked up your parcel"
        if ps in ("parcel_dropped_in", "present"):
            return "parcel_theft_detected", "Parcel delivered", "A parcel arrived at your door"
        if ps:
            return "parcel_theft_detected", "Parcel activity detected", "Parcel activity at your door"
        return None, None, None

    if label == "PERSON":
        loit = msg.get("loitering") or ""
        camera = msg.get("camera_name", "your camera")
        if loit and loit not in ("Unknown", "No"):
            return "loitering_detected", "Loitering Detected", \
                   f"Someone is loitering near your {camera}"
        # Plain person-spotted notification. Earlier this branch returned None
        # (silently dropping every non-loitering PERSON event), which broke
        # the customer-facing "Kevin was spotted" / "Unknown person spotted"
        # path. Now we fire on every PERSON event; the per-event_id throttle
        # in _handle_ai_event prevents floods from MQTT update bursts on the
        # same track.
        sub_label = (msg.get("sub_label") or "").strip()
        person_id = msg.get("person_id")
        is_named = bool(person_id) and sub_label and sub_label.lower() not in ("someone", "unknown")
        if is_named:
            return "person_spotted", f"{sub_label} spotted", \
                   f"{sub_label} was spotted at your {camera}"
        return "person_spotted", "Unknown person spotted", \
               f"An unknown person was spotted at your {camera}"

    # LoiterAI publishes label="LOITERING" with loitering field set to a
    # score/pattern string. Distinct from FaceAI's PERSON+loitering pattern.
    if label == "LOITERING":
        return "loitering_detected", "Loitering Detected", \
               f"Someone is loitering near your {msg.get('camera_name','camera')}"

    if label == "CAR":
        vs = (msg.get("vehicle_status") or "").strip()
        owner = (msg.get("recognized_name") or msg.get("owner") or "").strip()
        is_known = bool(owner) and "unknown" not in owner.lower()
        cam = msg.get("camera_name", "your camera")

        # Known-owner state-machine path (driveway "garage" use case) — unchanged.
        # The "garage_detected" notification_type also drives the garage door
        # automation downstream, so we keep this branch first and untouched.
        if is_known and vs == "Approaching":
            return "garage_detected", f"{owner}'s vehicle arriving", f"{owner} is pulling in"
        if is_known and vs in ("Parked", "Parked-LongTerm"):
            return "garage_detected", f"{owner}'s vehicle parked", f"{owner} has arrived"
        if is_known and vs == "Departing":
            return "garage_detected", f"{owner}'s vehicle leaving", f"{owner} is heading out"

        # Every other CAR event fires a generic "vehicle_spotted" banner. Body
        # text varies with state so users get context (arriving / parked /
        # leaving / just-spotted) without inheriting garage-automation routing.
        # Pre-fix: this whole branch silently returned None, dropping every
        # passing car a customer's front-door camera saw. Confirmed from a
        # Mill Valley DB probe: 5+ Spotted CAR events all dropped.
        state_phrase = {
            "Approaching":     "arriving",
            "Parked":          "parked",
            "Parked-LongTerm": "parked",
            "Departing":       "leaving",
        }.get(vs, "spotted")

        if is_known:
            return "vehicle_spotted", f"{owner}'s vehicle {state_phrase}", \
                   f"{owner}'s vehicle was {state_phrase} at your {cam}"
        if state_phrase == "spotted":
            return "vehicle_spotted", "Vehicle spotted", \
                   f"A vehicle was spotted at your {cam}"
        return "vehicle_spotted", f"Unknown vehicle {state_phrase}", \
               f"An unknown vehicle was {state_phrase} at your {cam}"

    return None, None, None


# ---------- APNs ----------

def _jwt_token():
    return jwt.encode(
        {"iss": TEAM_ID, "iat": int(time.time())},
        PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": KEY_ID},
    )


METRICS_LOG = "/var/log/la-publisher-metrics.jsonl"


def _emit_metric(push_type, log_tag, status, http_status=None, error=None):
    """Append one structured record per push attempt. Fleet-wide schema —
    later we can scrape these into CloudWatch / Argus. For now, ssh + jq the file."""
    try:
        rec = {
            "ts": time.time(),
            "hub_slug": os.environ.get("DEVICE_NAME", ""),
            "push_type": push_type,        # liveactivity | alert | fcm
            "tag": log_tag,                # e.g. "LA/garage_detected event=..." or "alert/loitering ..."
            "status": status,              # ok | http_fail | exception | skipped
            "http_status": http_status,
            "error": (str(error)[:200] if error else None),
        }
        with open(METRICS_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass  # Telemetry must never break the push path


def _apns_post(token, payload, push_type, topic, log_tag,
               environment="production", max_retries=2):
    """Thin wrapper over notifications.apns_client.send_apns_push that adds
    publisher-side telemetry + log lines. The actual APNs HTTP/2 logic lives
    in apns_client.py so the Django diagnostic endpoint and the publisher
    exercise identical code (no drift).

    Returns the same {"ok","stale","fail","skipped"} string codes as before
    so existing callers don't break.
    """
    # Lazy-import so the publisher's startup doesn't depend on Django being
    # bootstrappable. apns_client is Django-free by design.
    sys.path.insert(0, "/root/jupyter-hub-controller")
    from notifications.apns_client import send_apns_push  # noqa: PLC0415

    result = send_apns_push(
        token=token, payload=payload, push_type=push_type, topic=topic,
        team_id=TEAM_ID, key_id=KEY_ID, private_key=PRIVATE_KEY,
        environment=environment, max_retries=max_retries,
    )
    if result["result"] == "skipped":
        _emit_metric(push_type, log_tag, "skipped", error=result.get("reason"))
        return "skipped"
    if result["result"] == "ok":
        log.info("APNs OK %s (%dms)", log_tag, result["latency_ms"])
        _emit_metric(push_type, log_tag, "ok", http_status=200)
        return "ok"
    if result["result"] == "stale":
        log.warning("APNs stale token %s reason=%s — will remove",
                    log_tag, result.get("reason") or result.get("http_status"))
        _emit_metric(push_type, log_tag, "stale",
                     http_status=result["http_status"], error=result.get("reason"))
        return "stale"
    log.error("APNs %s status=%s reason=%s",
              log_tag, result["http_status"], result.get("reason"))
    _emit_metric(push_type, log_tag, "http_fail",
                 http_status=result["http_status"], error=result.get("reason"))
    return "fail"


def push_la_ai_event(notification_type, title, msg):
    event_id = msg.get("event_id") or ""
    state = {
        "notificationType": notification_type,
        "time": time.strftime("%-I:%M %p"),
        "cameraName": msg.get("camera_name", "Camera"),
        "title": title,
        "label": msg.get("label", ""),
        "isAlarmActive": False,
        "eventId": event_id,
        "videoPath": msg.get("video_path", "") or "",
        "audioPath": msg.get("audio_path", "") or "",
        "snapshotFilename": f"event_{event_id}.jpg" if event_id else "",
        "alarmActivatedAt": 0,
        "startedAt": time.time(),
    }
    label_lower = state["label"].lower() or "event"
    payload = {
        "aps": {
            "timestamp": int(time.time()),
            "event": "start",
            "content-state": state,
            "attributes-type": "LiveActivityWidgetAttributes",
            "attributes": {"name": f"{label_lower}-event-{event_id}"},
            "alert": {"title": title, "body": state["cameraName"]},
            "sound": "default",
        }
    }
    _apns_post(LA_TOKEN, payload, "liveactivity",
               f"{BUNDLE_ID}.push-type.liveactivity",
               f"LA/{notification_type} event={event_id}",
               environment=LIVE_ACTIVITY_ENVIRONMENT)


def push_la_halo(halo_name, charge_percent, is_charging, charge_time_min, wifi_quality, temperature_c):
    state = {
        "haloName": halo_name,
        "chargePercent": int(charge_percent),
        "isCharging": bool(is_charging),
        "chargeTimeRemainingMin": int(charge_time_min),
        "wifiSignalQuality": wifi_quality or "Unknown",
        "temperatureC": float(temperature_c or 0.0),
    }
    payload = {
        "aps": {
            "timestamp": int(time.time()),
            "event": "start",
            "content-state": state,
            "attributes-type": "HaloChargingActivityAttributes",
            "attributes": {},
            "alert": {"title": f"{halo_name} is charging", "body": f"{int(charge_percent)}%"},
            "sound": "default",
        }
    }
    _apns_post(HALO_TOKEN, payload, "liveactivity",
               f"{BUNDLE_ID}.push-type.liveactivity",
               f"LA/halo halo={halo_name}",
               environment=LIVE_ACTIVITY_ENVIRONMENT)


def push_la_halo_offboard_2fa(slug, alarm_id, nonce, serial, expires_in,
                              expires_at, title, body):
    """Fire iOS Live Activity push-to-start for the Halo offboard 2FA card.

    Triggered by hub Django publishing to /halo_offboard_2fa_pending after
    the Halo replies `pending` to a factory_reset request. The admin's
    iPhone shows a Live Activity (lock screen + Dynamic Island) with
    Confirm / Cancel buttons. If they tap Confirm, the Flutter app calls
    /api/halo/recovery/confirm with this nonce.

    Per HALO_2FA_FACTORY_RESET_BACKEND_BRIEF.md, the Halo's own internal
    timer auto-cancels after 60s, so we don't need a hub-side watchdog.
    """
    state = {
        "slug": slug,
        "alarmId": int(alarm_id),
        "nonce": int(nonce),
        "serial": serial,
        "title": title,
        "body": body,
        "expiresAt": int(expires_at),
        "expiresIn": int(expires_in),
        "startedAt": time.time(),
    }
    payload = {
        "aps": {
            "timestamp": int(time.time()),
            "event": "start",
            "content-state": state,
            "attributes-type": "HaloOffboardActivityAttributes",
            "attributes": {"slug": slug, "alarmId": int(alarm_id)},
            "alert": {"title": title, "body": body},
            "sound": "default",
        }
    }
    _apns_post(LA_TOKEN, payload, "liveactivity",
               f"{BUNDLE_ID}.push-type.liveactivity",
               f"LA/halo_offboard_2fa slug={slug} nonce={nonce}")


def push_apns_alert(title, body, data, log_tag):
    """Regular APNs banner notification — bypasses Firebase entirely. Fans
    out to every iPhone whose raw APNs token is registered. Each token's
    sandbox-vs-production environment is read from notification_tokens.json
    so debug-build tokens route to api.sandbox.push.apple.com and
    TestFlight/App-Store tokens route to api.push.apple.com. Tokens that
    come back as 410 Unregistered or 400 BadDeviceToken are auto-removed.
    """
    if not APNS_RAW_TOKENS:
        return
    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
            "mutable-content": 1,
        },
    }
    if data:
        payload.update({k: ("" if v is None else str(v)) for k, v in data.items()})
    # Build a token → environment map from the JSON store so we route per-token.
    # Tokens missing from the store (shouldn't happen, but defensive) fall back
    # to production — same as legacy behaviour.
    token_env = _read_token_environments()
    stale_tokens = []
    for tok in APNS_RAW_TOKENS:
        env = token_env.get(tok, "production")
        result = _apns_post(tok, payload, "alert", BUNDLE_ID,
                            f"alert/{log_tag} dev=...{tok[-6:]} env={env}",
                            environment=env)
        if result == "stale":
            stale_tokens.append(tok)
    if stale_tokens:
        _cleanup_stale_tokens(stale_tokens)


def _read_token_environments():
    """Return {apns_token: environment} from the JSON store. Empty dict on
    any read error so we fall back to production routing."""
    try:
        with open(TOKEN_STORE_PATH) as f:
            store = json.load(f)
        return {m["apns_token"]: m.get("environment", "production")
                for m in store.values() if "apns_token" in m}
    except Exception:
        return {}


TOKEN_STORE_PATH = "/root/jupyter-hub-controller/notification_tokens.json"
_token_store_lock = threading.Lock()


def _cleanup_stale_tokens(stale_tokens):
    """Remove dead APNs tokens from the JSON store + .env. Decoupled from
    Django so the publisher process doesn't need to bootstrap the framework.
    The Django side (notifications.token_store) writes the same file with
    the same lock semantics, so concurrent register + cleanup are safe.
    """
    if not stale_tokens:
        return
    stale_set = set(stale_tokens)
    try:
        with _token_store_lock:
            try:
                with open(TOKEN_STORE_PATH) as f:
                    store = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                store = {}
            keep = {dev_id: m for dev_id, m in store.items()
                    if m.get("apns_token") not in stale_set}
            removed = len(store) - len(keep)
            if removed:
                # Atomic write
                tmp = TOKEN_STORE_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(keep, f, indent=2, sort_keys=True)
                os.replace(tmp, TOKEN_STORE_PATH)
                # Sync to .env so the next refresh picks up the cleaned list
                _write_env_var("APNS_DEVICE_TOKENS",
                               ",".join(m["apns_token"] for m in keep.values()))
                log.info("removed %d stale APNs tokens, %d active remaining",
                         removed, len(keep))
    except Exception as e:
        log.warning("stale-token cleanup failed (will retry on next event): %s", e)
        return
    # Force in-process refresh so push_apns_alert in this same event-handle
    # cycle skips the just-removed tokens.
    try:
        _refresh_tokens()
    except Exception as e:
        log.warning("token refresh after cleanup failed: %s", e)


def _write_env_var(key, value):
    """Update one env var atomically. Same shape as _load_env's reader so we
    preserve other lines verbatim."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        lines = f.readlines()
    found = False
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            out.append(f"{key}={value}\n")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}\n")
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(out)
    os.replace(tmp, ENV_PATH)


# ---------- FCM (Android + iOS fallback) ----------

def push_fcm_notification(title, body, data=None):
    """Multicast FCM. Disabled by default since 2026-05-06 because direct-APNs
    is now proven end-to-end on iOS and the Firebase round-trip is wasted
    bandwidth (FCM project has no .p8 → iOS deliveries fail anyway). Set
    ENABLE_FCM_FALLBACK=1 in .env to re-enable, e.g. when shipping Android.
    """
    if os.environ.get("ENABLE_FCM_FALLBACK", "0") != "1":
        return
    if _firebase_app is None or not FCM_TOKENS:
        return
    safe_data = {k: ("" if v is None else str(v)) for k, v in (data or {}).items()}
    msg = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=safe_data,
        apns=messaging.APNSConfig(payload=messaging.APNSPayload(
            aps=messaging.Aps(
                alert=messaging.ApsAlert(title=title, body=body),
                sound="default",
                mutable_content=True,
            ),
        )),
        tokens=list(FCM_TOKENS),
    )
    try:
        resp = messaging.send_each_for_multicast(msg)
        log.info("FCM sent: success=%d failed=%d", resp.success_count, resp.failure_count)
        _emit_metric("fcm", title[:40], "ok" if resp.failure_count == 0 else "http_fail",
                     http_status=resp.success_count, error=f"failed={resp.failure_count}" if resp.failure_count else None)
    except Exception as ex:
        log.exception("FCM exception: %s", ex)
        _emit_metric("fcm", title[:40], "exception", error=ex)


# ---------- helpers ----------

def _wifi_quality(rssi):
    if rssi is None:
        return "Unknown"
    try:
        rssi = float(rssi)
    except (TypeError, ValueError):
        return "Unknown"
    if rssi >= -50: return "Excellent"
    if rssi >= -60: return "Good"
    if rssi >= -70: return "Average"
    return "Poor"


# ---------- handlers ----------

def _handle_ai_event(data):
    notification_type, title, body = classify_ai_event(data)
    if not notification_type:
        return
    label = data.get("label", "")
    event_id = data.get("event_id", "")
    ok, why = _throttle_ok(event_id, label)
    if not ok:
        log.info("skip: %s", why)
        return
    # Per-person / per-camera / per-notification_type cooldown — stacks
    # below event_id throttle. Catches the "Kevin spotted 3x in 30s on the
    # same camera across 3 distinct Frigate events" case that event_id
    # alone misses. See _person_cooldown_ok docstring for full design.
    ok2, why2 = _person_cooldown_ok(notification_type, data)
    if not ok2:
        log.info("skip: %s", why2)
        return
    # LA_SKIP_TYPES (defined at module level) lists notification_types that
    # are banner-only — no Live Activity card. Live Activity cards persist
    # on the lock screen with action buttons (e.g. Open/Close Garage on
    # garage_detected widgets) and should only fire for actionable events.
    if notification_type not in LA_SKIP_TYPES:
        push_la_ai_event(notification_type, title, data)
    extra = {"notificationType": notification_type, "label": label, "event_id": event_id,
             "camera_name": data.get("camera_name", ""), "video_path": data.get("video_path", "") or ""}
    push_apns_alert(title, body, extra, log_tag=notification_type)
    push_fcm_notification(title, body, data=extra)


def _handle_halo_status(topic, data):
    parts = [p for p in topic.split("/") if p]
    if len(parts) < 2 or parts[-1] != "status":
        return
    identity = parts[0]
    if not isinstance(data, dict):
        return
    is_charging = bool(data.get("charging", False))
    halo_name = data.get("device") or identity

    with _halo_lock:
        prev = _halo_charge_state.get(identity)
        _halo_charge_state[identity] = is_charging
    if prev is None and not is_charging: return
    if prev == is_charging: return

    log.info("halo charge edge: %s %s -> %s", identity, prev, is_charging)
    if not is_charging: return

    ok, why = _throttle_ok(f"halo-{identity}", "HALO")
    if not ok:
        log.info("skip halo: %s", why)
        return

    pct = int(data.get("battery_percent", 0))
    push_la_halo(halo_name, pct, is_charging,
                 int(data.get("charge_time_minutes", 0)),
                 _wifi_quality(data.get("wifi_rssi")),
                 float(data.get("temperature", 0.0)))
    title = f"{halo_name} is charging"
    body = f"{pct}% — placed on charger"
    extra = {"notificationType": "halo_charging", "halo_name": halo_name, "charge_percent": str(pct)}
    push_apns_alert(title, body, extra, log_tag="halo")
    push_fcm_notification(title, body, data=extra)


def _handle_halo_offboard_2fa(data):
    """Hub Django publishes here after the Halo issues a `pending` nonce."""
    try:
        push_la_halo_offboard_2fa(
            slug=data["slug"],
            alarm_id=data.get("alarm_id", 0),
            nonce=data["nonce"],
            serial=data.get("serial", data["slug"]),
            expires_in=data.get("expires_in", 60),
            expires_at=data.get("expires_at", int(time.time()) + 60),
            title=data.get("title", "Factory Reset Requested"),
            body=data.get("body", "Confirm Halo factory reset"),
        )
    except KeyError as ex:
        log.error("halo_offboard_2fa missing field: %s payload=%s", ex, data)


def _on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        return
    if msg.topic == "/events":
        _handle_ai_event(data)
    elif msg.topic == "/halo_offboard_2fa_pending":
        _handle_halo_offboard_2fa(data)
    elif msg.topic.endswith("/status"):
        _handle_halo_status(msg.topic, data)


def _on_connect(client, userdata, flags, rc, properties=None):
    log.info("MQTT connected rc=%s", rc)
    client.subscribe([
        ("/events", 0),
        ("+/status", 0),
        ("/halo_offboard_2fa_pending", 1),
    ])
    log.info("subscribed: /events  +/status  /halo_offboard_2fa_pending")


TOKEN_REFRESH_INTERVAL_SECONDS = 60


def _refresh_tokens():
    """Re-read .env and update token globals. Called every 60s by a daemon
    thread so the publisher picks up newly-registered tokens without needing
    a process restart, AND recovers from transient empty-token states (which
    used to crash-loop the service)."""
    global LA_TOKEN, HALO_TOKEN, FCM_TOKENS, APNS_RAW_TOKENS
    _load_env(force=True)
    new_la = os.environ.get("LIVE_ACTIVITY_START_TOKEN", "")
    new_halo = os.environ.get("HALO_CHARGING_START_TOKEN", "")
    new_fcm = [t for t in os.environ.get("FCM_REGISTRATION_IDS", "").split(",") if t]
    new_raw = [t for t in os.environ.get("APNS_DEVICE_TOKENS", "").split(",") if t]
    changed = (
        new_la != LA_TOKEN
        or new_halo != HALO_TOKEN
        or new_fcm != FCM_TOKENS
        or new_raw != APNS_RAW_TOKENS
    )
    LA_TOKEN, HALO_TOKEN, FCM_TOKENS, APNS_RAW_TOKENS = new_la, new_halo, new_fcm, new_raw
    if changed:
        log.info(
            "tokens refreshed: la=%s halo=%s fcm=%d apns_raw=%d",
            "set" if LA_TOKEN else "EMPTY",
            "set" if HALO_TOKEN else "EMPTY",
            len(FCM_TOKENS),
            len(APNS_RAW_TOKENS),
        )


def _token_refresh_loop():
    while True:
        time.sleep(TOKEN_REFRESH_INTERVAL_SECONDS)
        try:
            _refresh_tokens()
        except Exception as e:
            log.warning("token refresh failed (will retry): %s", e)


def main():
    log.info(
        "starting publisher v4  bundle=%s la=%s halo=%s fcm=%d apns_raw=%d firebase=%s",
        BUNDLE_ID,
        "set" if LA_TOKEN else "EMPTY",
        "set" if HALO_TOKEN else "EMPTY",
        len(FCM_TOKENS),
        len(APNS_RAW_TOKENS),
        "yes" if _firebase_app else "no",
    )
    # Previously: sys.exit(2) here when no tokens. That caused a crash-loop
    # under systemd whenever the .env was transiently empty (e.g., during a
    # hub-controller restart). 310+ restarts in one day = lost notifications
    # for ~9 hours straight. Now we keep MQTT alive and re-poll tokens every
    # 60s so the next refresh recovers automatically.
    if not (LA_TOKEN or HALO_TOKEN or FCM_TOKENS or APNS_RAW_TOKENS):
        log.warning(
            "no tokens at startup — staying alive, will re-check every %ds",
            TOKEN_REFRESH_INTERVAL_SECONDS,
        )

    threading.Thread(target=_token_refresh_loop, daemon=True).start()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="live-activity-publisher",
        clean_session=True,
    )
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect("127.0.0.1", 1883, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
