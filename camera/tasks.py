import logging
import os
import subprocess
from datetime import timedelta

from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from isodate import parse_datetime

from utils.api import APIClient
from utils.restarting_service import restart_service, restart_system_service
from utils.update_env import read_env_file


def render_and_write_config(template_name, context, output_path):
    """Render Django template and write to file. Returns True if content changed."""
    import os
    config = render_to_string(template_name, context)
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="UTF-8") as f:
                if f.read() == config:
                    return False
        except Exception:
            pass
    with open(output_path, "w", encoding="UTF-8") as config_file:
        config_file.write(config)
    return True


def safely_render_and_swap_frigate_config(template_name, context, output_path,
                                           container_name, health_timeout_s=30):
    """Render Frigate config with atomic-swap + auto-rollback.

    Pattern (the structural fix that prevents 'render breaks streams' incidents):
      1. Render new YAML in memory.
      2. If unchanged from current → return False, no restart.
      3. Backup current config → ``<output_path>.previous``.
      4. Write new config to ``output_path``.
      5. Restart Frigate container.
      6. Poll docker inspect for healthy state, max ``health_timeout_s``.
      7. If healthy → delete .previous, return True (success).
      8. If not healthy → restore .previous to output_path, restart Frigate
         (back onto known-good config), raise RuntimeError so caller knows.

    Worst-case impact of a bad render: ~30 s of stream interruption while we
    detect the failure + roll back, vs indefinite crash-loop with the old
    pattern. Streams stay up on the previous good config until next attempt.

    Always-restart-but-only-on-actual-change semantics preserved (step 2).
    """
    import os
    import time
    import logging as _logging

    log = _logging.getLogger(__name__)

    config = render_to_string(template_name, context)
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="UTF-8") as f:
                if f.read() == config:
                    return False  # no-op
        except Exception:
            pass

    previous_path = output_path + ".previous"
    backed_up = False
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="UTF-8") as src, \
                 open(previous_path, "w", encoding="UTF-8") as dst:
                dst.write(src.read())
            backed_up = True
        except Exception as e:
            log.error(f"safe-swap: failed to back up current config: {e}; aborting render")
            raise

    with open(output_path, "w", encoding="UTF-8") as f:
        f.write(config)

    try:
        restart_service(container_name)
    except Exception as e:
        log.error(f"safe-swap: restart_service raised: {e}")
        if backed_up:
            _rollback_frigate_config(previous_path, output_path, container_name)
        raise

    deadline = time.time() + health_timeout_s
    healthy = False
    while time.time() < deadline:
        try:
            out = subprocess.run(
                ["sudo", "docker", "inspect", container_name,
                 "--format", "{{.State.Health.Status}}"],
                capture_output=True, text=True, timeout=5,
            )
            status = (out.stdout or "").strip().lower()
            if status == "healthy":
                healthy = True
                break
            if status == "unhealthy":
                # fail fast — don't wait full deadline
                break
        except Exception:
            pass
        time.sleep(2)

    if not healthy:
        log.error(f"safe-swap: Frigate did not become healthy within "
                  f"{health_timeout_s}s; rolling back")
        if backed_up:
            _rollback_frigate_config(previous_path, output_path, container_name)
        raise RuntimeError(
            f"Frigate failed health check after config render. "
            f"Rolled back to previous config; streams should resume shortly."
        )

    # Success — Frigate is healthy on the new config
    if backed_up:
        try:
            os.unlink(previous_path)
        except Exception:
            pass
    log.info(f"safe-swap: Frigate validated healthy on new config")
    return True


def _rollback_frigate_config(previous_path, output_path, container_name):
    """Restore .previous → output_path and restart Frigate. Best-effort."""
    import os
    import logging as _logging
    log = _logging.getLogger(__name__)
    try:
        if os.path.exists(previous_path):
            with open(previous_path, "r", encoding="UTF-8") as src, \
                 open(output_path, "w", encoding="UTF-8") as dst:
                dst.write(src.read())
            os.unlink(previous_path)
            log.warning(f"safe-swap: rolled back to previous config at {output_path}")
        try:
            restart_service(container_name)
        except Exception as e:
            log.error(f"safe-swap: rollback restart failed: {e}")
    except Exception as e:
        log.exception(f"safe-swap: rollback itself failed: {e}")



def _zone_coords_to_pixels(coords, camera_type):
    """Convert normalized 0-1 zone coordinates to Frigate detect pixel coordinates."""
    if camera_type == "RING":
        w, h = 720, 720
    else:
        w, h = 1920, 1080
    pixels = []
    for i, val in enumerate(coords):
        if i % 2 == 0:
            pixels.append(str(round(val * w)))
        else:
            pixels.append(str(round(val * h)))
    return ",".join(pixels)


def get_cameras():
    model = apps.get_model("camera.camera")
    cameras = model.objects.filter(is_enabled=True)
    camera_data = []
    for camera in cameras:
        zones = [
            {
                "name": zone.zone_name,
                "coordinates": _zone_coords_to_pixels(
                    zone.coordinates,
                    camera.type,
                ),
                "objects": zone.objects_detect,
            }
            for zone in camera.camera_setting_zone.all()
        ]
        # 2026-05-03 — Vehicle AI detection zone. Stored as nested [[x,y]×4]
        # on Camera; flatten for the existing pixel-conversion helper which
        # expects [x1,y1,x2,y2,...]. Only published when set; AI engine and
        # Frigate gate detection on points-in-polygon when present.
        if camera.vehicle_detection_zone:
            flat = [v for pt in camera.vehicle_detection_zone for v in pt]
            zones.append({
                "name": "vehicle_detection_zone",
                "coordinates": _zone_coords_to_pixels(flat, camera.type),
                # Only "car" — the loaded RKNN object detector doesn't produce
                # truck/motorcycle/bus classes. Frigate rejects zone configs
                # that reference labels not present in objects.track. If the
                # detector swaps to a model that produces those classes,
                # extend this list AND the template's objects.track.
                "objects": ["car"],
            })
        # 2026-05-05 — Adaptive detect resolution. Cameras with vehicle detection
        # configured pull the MAIN stream at native resolution (clamped to 1920
        # max so a 4K camera doesn't tank the NPU). Other cameras keep using the
        # sub stream for NPU thrift. Probed at onboard via ffprobe; null fields
        # mean we never measured native res and fall back to current behaviour.
        has_vehicle_detection = bool(camera.vehicle_detection_zone)
        main_w = camera.main_stream_width
        main_h = camera.main_stream_height
        # Clamp to 1920 max width — never upscale, proportionally scale height.
        # For 480p (640x480) → stays 640x480. For 1080p (1920x1080) → stays 1080.
        # For 4K (3840x2160) → caps to 1920x1080. NPU stays in budget.
        if main_w and main_h and main_w > 1920:
            scale = 1920.0 / main_w
            detect_w = 1920
            detect_h = int(main_h * scale)
        else:
            detect_w = main_w
            detect_h = main_h
        camera_data.append(
            {
                "name": camera.slug_name,
                "rtsp_url": camera.rtsp_url,
                "sub_rtsp_url": getattr(camera, 'sub_rtsp_url', None),
                "ring_device_id": camera.ring_device_id,
                "type": camera.type,
                "is_audio": camera.is_audio,
                "zones": zones,
                "has_vehicle_detection": has_vehicle_detection,
                "main_stream_width": main_w,
                "main_stream_height": main_h,
                "sub_stream_width": camera.sub_stream_width,
                "sub_stream_height": camera.sub_stream_height,
                "detect_width": detect_w,
                "detect_height": detect_h,
                # MotionIQ — per-camera Frigate sensitivity profile.
                # `motion_settings` already returns the AWARE baseline for
                # vehicle cameras, so the template can render unconditionally.
                "motion_iq_profile": camera.motion_profile,
                "motion_iq_applicable": camera.motion_iq_applicable,
                "motion_settings": camera.motion_settings,
            }
        )

    # 2026-05-05 — Tiered detect-resolution cap by enabled camera count.
    # Frigate's CPU + memory + NPU load scale linearly with detect.width *
    # detect.height * fps * camera_count. A hub with 6 cameras at native
    # 1080p detect eats ~4x the resources of 4 cameras at 720p. We cap
    # automatically so a customer adding their 7th camera doesn't crash
    # Frigate. Vehicle cams stay protected longest because plate reading
    # genuinely needs the detail.
    n = len(camera_data)
    if n <= 4:
        cap_vehicle = (1920, 1080)
        cap_other = None  # use camera native sub
    elif n <= 6:
        cap_vehicle = (1920, 1080)
        cap_other = (1280, 720)
    elif n <= 8:
        cap_vehicle = (1280, 720)
        cap_other = (854, 480)
    else:
        cap_vehicle = (1024, 576)
        cap_other = (640, 360)

    def _apply_cap(w, h, cap):
        if not cap or not w or not h:
            return w, h
        cap_w, cap_h = cap
        if w <= cap_w:
            return w, h
        scale = cap_w / float(w)
        return cap_w, int(h * scale)

    for c in camera_data:
        cap = cap_vehicle if c["has_vehicle_detection"] else cap_other
        c["detect_width"], c["detect_height"] = _apply_cap(
            c["detect_width"], c["detect_height"], cap
        )
        # Apply same cap to sub_stream dims so non-vehicle cams' detect block
        # in the template (which currently reads sub_stream_width/height) sees
        # the capped values. Original native res still preserved on Camera row.
        if not c["has_vehicle_detection"]:
            c["sub_stream_width"], c["sub_stream_height"] = _apply_cap(
                c["sub_stream_width"], c["sub_stream_height"], cap
            )

    return camera_data


def _get_turn_from_db():
    """Fallback: read TURN credentials from local database."""
    try:
        from cloudflare_turn.models import Turn
        turn = Turn.objects.first()
        if not turn or not turn.credential:
            return None
        return {
            "credential": turn.credential,
            "previous_turn": turn.previous_turn,
        }
    except Exception as e:
        logging.error(f"Error reading TURN from DB: {e}")
        return None


def _get_turn_credentials():
    """Fetch TURN credentials for go2rtc WebRTC. Returns dict with stun/turn keys."""
    try:
        ice_response = get_ice_server()
        if not ice_response or ice_response == {}:
            logging.info("Cloud TURN API unavailable, using DB fallback")
            ice_response = _get_turn_from_db()
        if not ice_response:
            return {}

        ice_server = ice_response.get("previous_turn")
        if not ice_server:
            ice_server = ice_response
        else:
            created_at_str = ice_server.get("created_at")
            if created_at_str:
                created_at = parse_datetime(created_at_str)
                if created_at:
                    now = timezone.now()
                    expire_time = created_at + timedelta(days=2)
                    remaining_time = expire_time - now
                    if remaining_time < timedelta(hours=24):
                        ice_server = ice_response

        ice_credential = ice_server.get("credential")
        if not ice_credential or len(ice_credential) < 2:
            return {}

        all_turn_urls = ice_credential[1]["urls"]
        # Filter port 53 — browsers block TURN on DNS port
        safe_urls = [u for u in all_turn_urls if ":53?" not in u]
        # Use 2 TURN entries: UDP (fastest) + TURNS:443 (most reliable).
        # P2P via STUN is always priority. TURN is fallback for symmetric NAT.
        preferred_pair = [
            "turn:turn.cloudflare.com:3478?transport=udp",
            "turns:turn.cloudflare.com:443?transport=tcp",
        ]
        turn_urls = [u for u in preferred_pair if u in safe_urls]
        if not turn_urls:
            turn_urls = safe_urls[:2]

        return {
            "stun_server": ice_credential[0]["urls"],
            "turn_server": turn_urls,
            "turn_user": ice_credential[1]["username"],
            "turn_password": ice_credential[1]["credential"],
        }
    except Exception as e:
        logging.error(f"Error fetching TURN credentials: {e}")
        return {}


@shared_task
def update_frigate_config():
    """Render Frigate config with atomic-swap + auto-rollback.

    On a bad render (Frigate fails to start healthy on the new config), the
    task rolls the file back to the last known-good copy and restarts Frigate
    onto it. Streams interrupt for ~30 s during detection + rollback instead
    of indefinitely crash-looping. Caller (Celery beat) sees the raised error
    in the worker logs but doesn't propagate to the user-facing API.
    """
    camera_data = get_cameras()
    ice_data = _get_turn_credentials()
    try:
        changed = safely_render_and_swap_frigate_config(
            "frigate_config.yaml",
            {
                "cameras": camera_data,
                "mqtt_user": settings.MQTT_FRIGATE_USERNAME,
                "mqtt_password": settings.MQTT_FRIGATE_PASSWORD,
                **ice_data,
            },
            settings.FRIGATE_CONFIG_PATH,
            settings.FRIGATE_CONTAINER_NAME,
        )
    except RuntimeError as e:
        # Bad render. safely_render_and_swap_frigate_config has already
        # rolled back to .previous and restarted Frigate. Bubble up so
        # operators see the failure in Celery worker logs.
        logging.error(f"update_frigate_config: render rejected, rolled back: {e}")
        return f"Frigate config render failed; rolled back: {e}"
    if changed:
        return "Frigate config updated successfully."
    return "Frigate config unchanged - no restart needed."


def update_mediamtx_config():
    """Update MediaMTX config with camera streams and TURN credentials.

    MediaMTX serves as the WHEP endpoint for Flutter app WebRTC streaming.
    The CF tunnel carries signaling (HTTP), while media flows via STUN P2P
    or Cloudflare TURN relay.
    """
    try:
        camera_data = get_cameras()
        ice_data = _get_turn_credentials()

        if not ice_data:
            logging.warning("MediaMTX: TURN credentials unavailable, "
                            "config will have cameras but no TURN relay")

        changed = render_and_write_config(
            "mediamtx.yml",
            {
                "cameras": camera_data,
                **ice_data,
            },
            settings.MEDIAMTX_CONFIG_PATH,
        )

        if changed:
            restart_system_service("mediamtx")
            return "MediaMTX config updated successfully."
        return "MediaMTX config unchanged - no restart needed."
    except Exception as e:
        logging.error(f"Error executing update mediamtx config: {e}")
        return


@shared_task
def update_camera_config():
    try:
        # Update Frigate config (go2rtc streams + RKNN detector + hwaccel)
        update_frigate_config.delay()
        # Update MediaMTX config (HLS + WebRTC paths sourced from go2rtc)
        update_mediamtx_config()
        return "Camera config updated successfully."
    except Exception as e:
        return f"Error updating camera config: {str(e)}"


@shared_task
def camera_setting_config(
    is_enabnled: bool, container_name: str, servicer_path, camera_name=None
):
    camera_file_path = servicer_path

    # Unlock immutable flag (set by ota-lockdown.sh to prevent OTA overwrites)
    subprocess.run(["chattr", "-i", camera_file_path], capture_output=True)

    try:
        with open(camera_file_path, "r", encoding="UTF-8") as file:
            lines = file.readlines()

        has_enabled = False
        has_camera_name = False

        updated_lines = []

        for line in lines:
            if line.strip().startswith("IS_ENABNLED"):
                updated_lines.append(f"IS_ENABNLED = {is_enabnled}\n")
                has_enabled = True
            elif line.strip().startswith("CAMERA_NAME") and not line.strip().startswith("CAMERA_NAME_LIST"):
                updated_lines.append(f"CAMERA_NAME = '{camera_name or ''}'\n")
                has_camera_name = True
            else:
                updated_lines.append(line)

        if not has_enabled:
            updated_lines.append(f"\nIS_ENABNLED = {is_enabnled}\n")

        if not has_camera_name:
            updated_lines.append(f"CAMERA_NAME = '{camera_name or ''}'\n")

        with open(camera_file_path, "w", encoding="UTF-8") as file:
            file.writelines(updated_lines)

        logging.info(f"[camera_setting_config] {container_name}: IS_ENABNLED={is_enabnled}, CAMERA_NAME={camera_name}")
    finally:
        # Re-lock immutable flag
        subprocess.run(["chattr", "+i", camera_file_path], capture_output=True)

    restart_service(container_name)
    return f"{container_name} config updated successfully."


# ---------------------------------------------------------------------------
# AI bind-mount constants healer
# ---------------------------------------------------------------------------
# Each AI Docker container (number_plate_detection, face_recognition,
# parcel_detection, sound_detection, …) bind-mounts /usr/src/app/constants.py
# from a host file at /root/jupyter-container/<image>/constants.py. That host
# file mixes two kinds of values in the same file:
#   (a) developer code-level defaults (thresholds, model paths, etc.)
#   (b) app-managed runtime values written by camera_setting_config above:
#       IS_ENABNLED  — feature toggle from CameraSetting model
#       CAMERA_NAME  — bound camera slug from CameraSetting FK
#
# When an AI image rebuild copies a fresh source over the bind-mount, the
# developer defaults arrive (good) but the (b) values get clobbered (bad —
# resets feature off + camera unbound). The user has to toggle in the app
# again to restore.
#
# This periodic task auto-heals (b) by reading the canonical state from the
# Django CameraSetting singleton and re-applying the values. It is the same
# write path the app toggle uses (camera_setting_config), so behaviour is
# identical — just kicked by a clock instead of a user click.
#
# Survives offboarding: this task lives in code (camera/tasks.py) and the
# beat schedule entry lives in version-controlled settings. Both ship in any
# gold-image build. No state files to lose.
#
# Survives onboarding: on a fresh hub, CameraSetting may be empty (singleton
# created on first save). The healer no-ops gracefully when no row exists.
# Once the user toggles via app, CameraSetting is populated and the next
# beat tick keeps the bind-mount in sync forever.

# Mapping of AI containers → (CameraSetting field for is_enabled,
# CameraSetting FK field for camera, settings.<NAME>_CONTAINER, settings path)
# Driven entirely by django settings (env-overridable in local.py) — no
# hardcoded paths in this file.
AI_HEALER_CONTAINERS = (
    {
        'kind': 'vehicle',
        'enabled_field': 'license_vehicle_recognition',
        'camera_fk_field': 'vehicle_recognition_camera',
        'container_setting': 'VEHICLE_CONFIG_NAME',
        'path_setting': 'VEHICLE_CONFIG_PATH',
    },
    {
        'kind': 'parcel',
        'enabled_field': 'enable_parcel_detect',
        'camera_fk_field': 'parcel_detect_camera',
        'container_setting': 'PARCEL_CONTAINER_NAME',
        'path_setting': 'PARCEL_CONFIG_PATH',
    },
    {
        'kind': 'face',
        'enabled_field': 'enable_face_recognition',
        'camera_fk_field': None,  # face has no per-camera binding
        'container_setting': 'FACIAL_CONTAINER_NAME',
        'path_setting': 'FACIAL_CONFIG_PATH',
    },
    {
        'kind': 'sound',
        'enabled_field': 'activate_sounds_detection',
        'camera_fk_field': None,
        'container_setting': 'SOUND_DETECTION_CONTAINER',
        'path_setting': 'SOUND_DETECTION_PATH',
    },
)


def _read_app_managed_lines(path):
    """Return (is_enabnled_str, camera_name_str) from a constants.py file.
    Returns (None, None) if the file is missing/unreadable."""
    if not path or not os.path.exists(path):
        return None, None
    is_enabnled = None
    camera_name = None
    try:
        with open(path, 'r', encoding='UTF-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('IS_ENABNLED') and '=' in stripped:
                    is_enabnled = stripped.split('=', 1)[1].strip()
                elif (stripped.startswith('CAMERA_NAME')
                      and not stripped.startswith('CAMERA_NAME_LIST')
                      and '=' in stripped):
                    camera_name = stripped.split('=', 1)[1].strip().strip("'").strip('"')
    except Exception as e:
        logging.debug(f"[ai-healer] read {path}: {e}")
        return None, None
    return is_enabnled, camera_name


@shared_task
def heal_ai_constants():
    """Periodic auto-healer for AI bind-mounted constants.py files.

    For each configured AI container:
      1. Read the current IS_ENABNLED and CAMERA_NAME from the bind-mount.
      2. Read the desired state from the Django CameraSetting singleton.
      3. If they drift, call camera_setting_config to re-apply the desired
         values (same path as the user-toggle handler).

    Idempotent. Runs at the cadence in CELERY_BEAT_SCHEDULE
    (default 60 seconds, env-tunable via AI_HEALER_INTERVAL_S).
    """
    CameraSettingModel = apps.get_model('camera', 'CameraSetting')
    cs = CameraSettingModel.objects.first()
    if cs is None:
        logging.debug("[ai-healer] No CameraSetting row yet (pre-onboarding) — skipping")
        return "no-camera-setting"

    healed = 0
    skipped = 0
    for spec in AI_HEALER_CONTAINERS:
        path = getattr(settings, spec['path_setting'], None)
        container = getattr(settings, spec['container_setting'], None)
        if not path or not container:
            logging.debug(f"[ai-healer] {spec['kind']}: settings missing, skip")
            skipped += 1
            continue
        if not os.path.exists(path):
            logging.debug(f"[ai-healer] {spec['kind']}: bind-mount {path} missing, skip")
            skipped += 1
            continue

        # Desired state from Django
        desired_enabled = bool(getattr(cs, spec['enabled_field'], False))
        desired_camera = ''
        if spec.get('camera_fk_field') and desired_enabled:
            cam = getattr(cs, spec['camera_fk_field'], None)
            if cam is not None:
                desired_camera = getattr(cam, 'slug_name', '') or ''

        # Current state on disk
        cur_enabled_raw, cur_camera = _read_app_managed_lines(path)
        # Normalize current "True"/"False" string to bool for compare
        cur_enabled = (str(cur_enabled_raw).strip() == 'True') if cur_enabled_raw is not None else None

        drift = (cur_enabled != desired_enabled) or ((cur_camera or '') != (desired_camera or ''))
        if not drift:
            logging.debug(f"[ai-healer] {spec['kind']}: in sync (enabled={cur_enabled}, camera={cur_camera!r})")
            continue

        logging.info(
            f"[ai-healer] {spec['kind']} drift detected — "
            f"on-disk=(IS_ENABNLED={cur_enabled_raw}, CAMERA_NAME={cur_camera!r}) "
            f"desired=(IS_ENABNLED={desired_enabled}, CAMERA_NAME={desired_camera!r}) → re-applying"
        )
        try:
            # Reuse the existing writer — same chattr/sed/chattr dance the
            # user-toggle handler runs. Synchronous so we can log success.
            camera_setting_config(
                is_enabnled=desired_enabled,
                container_name=container,
                servicer_path=str(path),
                camera_name=desired_camera or None,
            )
            healed += 1
        except Exception as e:
            logging.error(f"[ai-healer] {spec['kind']} heal failed: {e}")
    return f"healed={healed} skipped={skipped} total={len(AI_HEALER_CONTAINERS)}"


def get_ice_server():
    try:

        logging.info("Hub auto-restart cloudflare turn")
        hub_api = APIClient()

        slug_name = read_env_file("DEVICE_NAME")
        hub_secret = read_env_file("HUB_SECRET")

        response, api_result = hub_api.revokeTurnsCredential(
            slug_name=slug_name, hub_secret=hub_secret
        )
        logging.info(f"get credential response: {response}")

        return response
    except Exception as e:
        logging.error(f"Error executing get ice server: {e}")
        return {}


CAMERA_FAILURE_THRESHOLD = 3  # 3 × 5 min = 15 min before auto-disable


def _publish_camera_health_event(camera_slug, event_type):
    """Fire-and-forget MQTT notification for camera health changes."""
    import json as _json
    try:
        from utils.mqtt_client import MQTTClient
        from django.conf import settings as _settings
        mqtt_client = MQTTClient(
            host=_settings.MQTT_HOST,
            port=_settings.MQTT_PORT,
            username=_settings.MQTT_USERNAME,
            password=_settings.MQTT_PASSWORD,
            client_id="camera-health",
        )
        mqtt_client.connect()
        mqtt_client.publish(
            f"jupyter/camera/{camera_slug}/health",
            _json.dumps({"event": event_type, "camera": camera_slug}),
            qos=0,
        )
        mqtt_client.close()
    except Exception as e:
        logging.warning(f"MQTT camera health publish failed: {e}")


@shared_task
def monitor_camera_ips():
    """Check each RTSP camera IP. Auto-disable after sustained failure, re-enable on recovery."""
    from camera.enums import CameraType
    from camera.models import Camera
    from alarm.network import find_ip_by_mac, get_mac_address, ping_host

    # Iterate ALL RTSP cameras (including disabled) so we detect recovery
    cameras = Camera.objects.filter(type=CameraType.RTSP)
    if not cameras.exists():
        return "No RTSP cameras"

    config_changed = False
    results = []
    now = timezone.now()

    for camera in cameras:
        # Step 1: Backfill MAC if missing
        if not camera.mac_address and camera.ip:
            mac = get_mac_address(camera.ip)
            if mac:
                camera.mac_address = mac
                camera.save(update_fields=["mac_address"])
                logging.info(f"Backfilled MAC for {camera.slug_name}: {mac}")

        # Step 2: Ping stored IP (5s timeout — some cameras respond slowly)
        reachable = camera.ip and ping_host(camera.ip, timeout=5)

        # Step 3: If unreachable, ARP sweep to find new IP
        new_ip = None
        if not reachable and camera.mac_address:
            new_ip = find_ip_by_mac(camera.mac_address, populate_arp=True)
            if new_ip and new_ip != camera.ip:
                old_ip = camera.ip
                if camera.rtsp_url and old_ip:
                    camera.rtsp_url = camera.rtsp_url.replace(old_ip, new_ip)
                if camera.sub_rtsp_url and old_ip:
                    camera.sub_rtsp_url = camera.sub_rtsp_url.replace(old_ip, new_ip)
                camera.ip = new_ip
                update_fields = ["ip", "rtsp_url", "sub_rtsp_url"]
                if not camera.mac_address:
                    mac = get_mac_address(new_ip)
                    if mac:
                        camera.mac_address = mac
                        update_fields.append("mac_address")
                camera.save(update_fields=update_fields)
                config_changed = True
                reachable = True
                logging.info(f"Camera {camera.slug_name} IP updated: {old_ip} -> {new_ip}")
                results.append(f"{camera.slug_name}: moved {old_ip} -> {new_ip}")
            elif new_ip:
                reachable = True

        # Step 4: Health watchdog — track failures, auto-disable/enable
        if reachable:
            was_disabled = not camera.is_enabled
            camera.consecutive_failures = 0
            camera.last_seen_at = now
            update_fields = ["consecutive_failures", "last_seen_at"]
            if was_disabled:
                camera.is_enabled = True
                update_fields.append("is_enabled")
                config_changed = True
                logging.info(f"Camera {camera.slug_name} back online — re-enabled")
                _publish_camera_health_event(camera.slug_name, "camera_online")
            camera.save(update_fields=update_fields)
            if not any(camera.slug_name in r for r in results):
                results.append(f"{camera.slug_name}: OK at {camera.ip}")
        else:
            camera.consecutive_failures += 1
            update_fields = ["consecutive_failures"]
            if camera.consecutive_failures >= CAMERA_FAILURE_THRESHOLD and camera.is_enabled:
                camera.is_enabled = False
                update_fields.append("is_enabled")
                config_changed = True
                logging.warning(
                    f"Camera {camera.slug_name} disabled after "
                    f"{camera.consecutive_failures} consecutive failures"
                )
                _publish_camera_health_event(camera.slug_name, "camera_offline")
            camera.save(update_fields=update_fields)
            results.append(
                f"{camera.slug_name}: OFFLINE "
                f"({camera.consecutive_failures}/{CAMERA_FAILURE_THRESHOLD})"
            )

    # Batch config regeneration — one call covers all changes
    if config_changed:
        update_camera_config.delay()

    return "; ".join(results)


@shared_task
def restart_ring_safe():
    """Restart ring-mqtt container via docker compose"""
    try:
        logging.info("Stopping ring-mqtt...")
        subprocess.run(
            ["sudo", "docker", "compose", "down", settings.RING_STREAM_CONTAINER],
            cwd="../",
            check=True,
        )
        logging.info("Starting ring-mqtt...")
        subprocess.run(
            ["sudo", "docker", "compose", "up", "-d", settings.RING_STREAM_CONTAINER],
            cwd="../",
            check=True,
        )
        logging.info("ring-mqtt restarted successfully")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to restart ring-mqtt: {e}")


@shared_task
def cleanup_ring_device(ring_device_id):
    """Purge all traces of a Ring device after removal from Django.

    Cleans:
      1. ring-state.json (ring-mqtt device config)
      2. Home Assistant registries (entity, device, restore_state)

    Does NOT restart ring-mqtt or HA — ring-mqtt discovers ALL devices
    on the Ring account via API, so restarting HA would trigger
    re-publication of the unwanted device's MQTT discovery messages.
    """
    import json
    import os

    if not ring_device_id:
        logging.warning("cleanup_ring_device called with empty device ID")
        return

    logging.info(f"Cleaning up Ring device {ring_device_id}")

    # 1. Remove from ring-state.json
    ring_state_path = "/root/jupyter-container/ring-mqtt-data/ring-state.json"
    if os.path.exists(ring_state_path):
        try:
            with open(ring_state_path) as f:
                state = json.load(f)
            devices = state.get("devices", {})
            if ring_device_id in devices:
                del devices[ring_device_id]
                state["devices"] = devices
                with open(ring_state_path, "w") as f:
                    json.dump(state, f)
                logging.info(f"Removed {ring_device_id} from ring-state.json")
        except Exception as e:
            logging.error(f"Failed to clean ring-state.json: {e}")

    # 2. Clean Home Assistant registries
    ha_storage = "/root/jupyter-container/hass/config/.storage"
    for filename in ["core.restore_state", "core.entity_registry", "core.device_registry"]:
        filepath = os.path.join(ha_storage, filename)
        if not os.path.exists(filepath):
            continue
        try:
            with open(filepath) as f:
                data = json.load(f)

            data_section = data.get("data", {})
            modified = False

            if filename == "core.restore_state":
                if isinstance(data_section, list):
                    before = len(data_section)
                    data["data"] = [s for s in data_section if ring_device_id not in json.dumps(s)]
                    modified = len(data["data"]) != before
                elif isinstance(data_section, dict) and "states" in data_section:
                    before = len(data_section["states"])
                    data_section["states"] = [s for s in data_section["states"] if ring_device_id not in json.dumps(s)]
                    modified = len(data_section["states"]) != before
            elif filename == "core.entity_registry":
                entities = data_section.get("entities", [])
                before = len(entities)
                data_section["entities"] = [e for e in entities if ring_device_id not in json.dumps(e)]
                modified = len(data_section["entities"]) != before
            elif filename == "core.device_registry":
                devices_list = data_section.get("devices", [])
                before = len(devices_list)
                data_section["devices"] = [d for d in devices_list if ring_device_id not in json.dumps(d)]
                modified = len(data_section["devices"]) != before

            if modified:
                with open(filepath, "w") as f:
                    json.dump(data, f)
                logging.info(f"Cleaned {ring_device_id} from {filename}")
        except Exception as e:
            logging.error(f"Failed to clean {filename}: {e}")

    logging.info("Ring device cleanup complete (no container restart)")
    return f"Ring device {ring_device_id} cleanup complete"


# Single source of truth — must match camera.views.CameraSnapshotProxyView.THUMBNAIL_DIR.
# Defined in settings/common.py so the task and the view can never diverge again.
SNAPSHOT_DIR = getattr(
    settings,
    "CAMERA_THUMBNAILS_DIR",
    "/root/jupyter-hub-controller/media/thumbnails",
)


@shared_task
def capture_camera_snapshots():
    """Pre-warm /media/thumbnails/<slug>.jpg for every enabled camera.

    Two-source fallback per camera so a transient RTSP failure (busy stream,
    network blip) never leaves a stale thumbnail:

      1. RTSP via ffmpeg (RTSP-type cameras only). Uses sub_rtsp_url if
         available — it's lower-bandwidth, faster to grab a single frame.
      2. Frigate latest.jpg (works for ALL camera types including Ring,
         which routes through go2rtc → Frigate).

    Whichever succeeds first writes the thumbnail. If both fail in the same
    cycle, the previous thumbnail stays in place — staleness, not 404.

    Called by Celery beat (see CELERY_BEAT_SCHEDULE in settings/common.py).
    """
    import os as _os
    import urllib.request
    from camera.enums import CameraType
    from camera.models import Camera

    _os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    cameras = Camera.objects.filter(is_enabled=True)
    if not cameras.exists():
        return "No enabled cameras"

    def _try_rtsp(rtsp_url, tmp_path):
        """Returns True on success."""
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-hide_banner", "-loglevel", "error",
                    "-rtsp_transport", "tcp",
                    "-timeout", "3000000",  # 3s socket timeout (microseconds)
                    "-i", rtsp_url,
                    "-frames:v", "1",
                    "-q:v", "5",
                    tmp_path,
                ],
                capture_output=True,
                timeout=8,
            )
            return proc.returncode == 0 and _os.path.exists(tmp_path) and _os.path.getsize(tmp_path) > 100
        except (subprocess.TimeoutExpired, Exception):
            return False

    def _try_frigate(slug, output_path):
        """Returns True on success."""
        try:
            url = f"http://127.0.0.1:5000/api/{slug}/latest.jpg"
            with urllib.request.urlopen(url, timeout=2) as resp:
                content = resp.read()
                if len(content) > 100:
                    with open(output_path, "wb") as f:
                        f.write(content)
                    return True
        except Exception:
            pass
        return False

    ok = 0
    failed = []
    for camera in cameras:
        slug = camera.slug_name
        output_path = _os.path.join(SNAPSHOT_DIR, f"{slug}.jpg")
        tmp_path = f"{output_path}.tmp"

        captured = False

        # 1. RTSP first for RTSP-type cameras
        if camera.type == CameraType.RTSP:
            rtsp_url = camera.sub_rtsp_url or camera.rtsp_url
            if rtsp_url and _try_rtsp(rtsp_url, tmp_path):
                _os.replace(tmp_path, output_path)
                ok += 1
                captured = True

        # 2. Frigate fallback for everything (incl. Ring)
        if not captured and _try_frigate(slug, output_path):
            ok += 1
            captured = True

        if not captured:
            failed.append(slug)

        # Cleanup any leftover tmp file
        if _os.path.exists(tmp_path):
            try:
                _os.remove(tmp_path)
            except OSError:
                pass

    msg = f"thumbnails refreshed: {ok}/{cameras.count()} OK"
    if failed:
        msg += f"; failed: {','.join(failed)}"
    logging.info(msg)
    return msg


@shared_task
def probe_empty_onvif_fields():
    """Background self-heal for cameras with empty onvif_manufacturer / onvif_model.

    Inline ONVIF probe in camera/managers.py:323-328 only runs during the
    RTSPDiscoverView/Create flow. Cameras added via other paths (manual restore,
    cloned hub, migrated DB row, or restored from backup) end up with empty
    fields forever.

    This task walks RTSP cameras with non-empty IP+credentials and empty
    manufacturer, then calls get_onvif_device_info() and persists results.
    Skips cameras without credentials (probe always fails on those — operator
    has to enter creds first) and skips Ring cameras (different code path,
    handled by RingCameraSerializer.create()).

    Idempotent. Safe to run on every cycle. Called by Celery beat (see
    CELERY_BEAT_SCHEDULE in settings/common.py).
    """
    from camera.enums import CameraType
    from camera.models import Camera, RTSPCamera

    candidates = Camera.objects.filter(
        type=CameraType.RTSP,
        is_enabled=True,
        ip__isnull=False,
    ).exclude(ip="").exclude(
        username__isnull=True,
    ).exclude(username="").filter(
        onvif_manufacturer__in=[None, ""],
    )

    if not candidates.exists():
        return "No RTSP cameras need an ONVIF probe"

    updated = 0
    failed = []
    for camera in candidates:
        try:
            result = RTSPCamera.objects.get_onvif_device_info(
                camera.ip,
                username=camera.username or "",
                password=camera.password or "",
            )
            if result and (result.get("manufacturer") or result.get("model")):
                camera.onvif_manufacturer = result.get("manufacturer", "")
                camera.onvif_model = result.get("model", "")
                camera.save(update_fields=[
                    "onvif_manufacturer", "onvif_model", "updated_at",
                ])
                updated += 1
                logging.info(
                    f"probe_empty_onvif_fields: {camera.slug_name} → "
                    f"{result.get('manufacturer')!r} / {result.get('model')!r}"
                )
            else:
                failed.append(camera.slug_name)
        except Exception:
            logging.exception(
                f"probe_empty_onvif_fields: unexpected error for {camera.slug_name}"
            )
            failed.append(camera.slug_name)

    return f"Updated {updated}, failed {len(failed)} ({failed[:5]}{'...' if len(failed) > 5 else ''})"
