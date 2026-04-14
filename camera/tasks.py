import logging
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


def get_cameras():
    model = apps.get_model("camera.camera")
    cameras = model.objects.all()
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
                        "coordinates": ",".join(
                            str(round(x * 1000)) for x in zone.coordinates
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
        # Use all safe TURN URLs for maximum connectivity
        turn_urls = safe_urls

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


@shared_task
def monitor_camera_ips():
    """Check each RTSP camera IP. If unreachable, ARP sweep to find new IP."""
    from camera.enums import CameraType
    from camera.models import Camera
    from alarm.network import find_ip_by_mac, get_mac_address, ping_host

    cameras = Camera.objects.filter(type=CameraType.RTSP)
    if not cameras.exists():
        return "No RTSP cameras"

    config_changed = False
    results = []

    for camera in cameras:
        # Step 1: Backfill MAC if missing
        if not camera.mac_address and camera.ip:
            mac = get_mac_address(camera.ip)
            if mac:
                camera.mac_address = mac
                camera.save(update_fields=["mac_address"])
                logging.info(f"Backfilled MAC for {camera.slug_name}: {mac}")

        # Step 2: Ping stored IP
        if camera.ip and ping_host(camera.ip):
            results.append(f"{camera.slug_name}: OK at {camera.ip}")
            continue

        # Step 3: IP unreachable — ARP sweep if we have MAC
        new_ip = None
        if camera.mac_address:
            new_ip = find_ip_by_mac(camera.mac_address, populate_arp=True)

        # Step 4: Found at new IP — update DB and RTSP URL
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
            logging.info(f"Camera {camera.slug_name} IP updated: {old_ip} -> {new_ip}")
            results.append(f"{camera.slug_name}: moved {old_ip} -> {new_ip}")
        elif new_ip:
            results.append(f"{camera.slug_name}: recovered at {new_ip}")
        else:
            logging.warning(f"Camera {camera.slug_name} unreachable")
            results.append(f"{camera.slug_name}: OFFLINE")

    # Batch config regeneration — one call covers all IP changes
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
