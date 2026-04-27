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
        camera_data.append(
            {
                "name": camera.slug_name,
                "rtsp_url": camera.rtsp_url,
                "sub_rtsp_url": getattr(camera, 'sub_rtsp_url', None),
                "ring_device_id": camera.ring_device_id,
                "type": camera.type,
                "is_audio": camera.is_audio,
                "zones": [
                    {
                        "name": zone.zone_name,
                        "coordinates": _zone_coords_to_pixels(
                            zone.coordinates,
                            camera.type,
                        ),
                        "objects": zone.objects_detect,
                    }
                    for zone in camera.camera_setting_zone.all()
                ],
            }
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
    camera_data = get_cameras()

    # Fetch TURN credentials for go2rtc WebRTC
    ice_data = _get_turn_credentials()

    changed = render_and_write_config(
        "frigate_config.yaml",
        {
            "cameras": camera_data,
            "mqtt_user": settings.MQTT_FRIGATE_USERNAME,
            "mqtt_password": settings.MQTT_FRIGATE_PASSWORD,
            **ice_data,
        },
        settings.FRIGATE_CONFIG_PATH,
    )

    if changed:
        restart_service(settings.FRIGATE_CONTAINER_NAME)
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
    with open(camera_file_path, "r", encoding="UTF-8") as file:
        lines = file.readlines()

    has_enabled = False
    has_camera_name = False

    updated_lines = []

    for line in lines:
        if line.strip().startswith("IS_ENABNLED"):
            updated_lines.append(f"IS_ENABNLED = {is_enabnled}\n")
            has_enabled = True
        elif line.strip().startswith("CAMERA_NAME"):
            updated_lines.append(f"CAMERA_NAME = '{camera_name}'\n")
            has_camera_name = True
        else:
            updated_lines.append(line)

    # If missing, add new one
    if not has_enabled:
        updated_lines.append(f"\nIS_ENABNLED = {is_enabnled}\n")

    if not has_camera_name:
        updated_lines.append(f"CAMERA_NAME = {camera_name}\n")

    # Write and update the file
    with open(camera_file_path, "w", encoding="UTF-8") as file:
        file.writelines(updated_lines)

    restart_service(container_name)
    return f"{container_name} config updated successfully."


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


SNAPSHOT_DIR = os.path.join(
    getattr(settings, 'BASE_DIR', '/root/jupyter-hub-controller'),
    'media', 'thumbnails',
)


@shared_task
def capture_camera_snapshots():
    """Grab one JPEG frame from each RTSP camera, bypassing Frigate entirely."""
    import os as _os
    from camera.enums import CameraType
    from camera.models import Camera

    _os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    cameras = Camera.objects.filter(is_enabled=True, type=CameraType.RTSP)
    if not cameras.exists():
        return "No enabled RTSP cameras"

    results = []
    for camera in cameras:
        rtsp_url = camera.sub_rtsp_url or camera.rtsp_url
        if not rtsp_url:
            results.append(f"{camera.slug_name}: no RTSP URL")
            continue

        output_path = _os.path.join(SNAPSHOT_DIR, f"{camera.slug_name}.jpg")
        tmp_path = f"{output_path}.tmp"

        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-rtsp_transport", "tcp",
                    "-i", rtsp_url,
                    "-frames:v", "1",
                    "-q:v", "5",
                    tmp_path,
                ],
                capture_output=True,
                timeout=15,
            )
            if proc.returncode == 0 and _os.path.exists(tmp_path):
                _os.replace(tmp_path, output_path)
                results.append(f"{camera.slug_name}: OK")
            else:
                logging.warning(f"Snapshot failed for {camera.slug_name}: {proc.stderr[-200:]}")
                results.append(f"{camera.slug_name}: ffmpeg error")
        except subprocess.TimeoutExpired:
            results.append(f"{camera.slug_name}: timeout")
        except Exception as e:
            results.append(f"{camera.slug_name}: {e}")
        finally:
            if _os.path.exists(tmp_path):
                _os.remove(tmp_path)

    return "; ".join(results)
