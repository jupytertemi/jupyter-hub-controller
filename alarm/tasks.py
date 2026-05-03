import json
import logging
import subprocess
import time
from typing import Optional

from celery import shared_task
from django.conf import settings

from utils.restarting_service import restart_service, start_service, stop_service

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# v1.6 Halo onboard tasks
# --------------------------------------------------------------------------

@shared_task(bind=True, max_retries=10, default_retry_delay=60,
             queue="hub_operations_queue")
def enrich_and_publish_ha_discovery(self, device_id: int):
    """Off-thread enrichment + HA Auto-Discovery republish.

    The webhook handler writes the minimum AlarmDevice row immediately;
    this task does the slower /api/status query, fills missing fields,
    and republishes HA discovery on any field change.

    Idempotent: safe to call repeatedly. Republish-if-needed semantics
    mean we won't spam HA on register heartbeats.
    """
    from alarm.models import AlarmDevice, HaDiscoveryState
    from alarm.services.halo_enrichment import fetch_status, merge_enrichment

    try:
        device = AlarmDevice.objects.get(id=device_id)
    except AlarmDevice.DoesNotExist:
        logger.warning("enrich_skipped_device_gone id=%s", device_id)
        return

    needs_enrichment = not device.version_fw or device.name in ("", device.identity_name)

    if needs_enrichment:
        # Try /api/status; one shot per Celery attempt — outer retry loop
        # handles "Halo not ready yet" via Celery's max_retries.
        status = fetch_status(device.ip_address, timeout=2.0)
        enrichment = merge_enrichment(device.identity_name, status)
        fields_changed = []
        if enrichment.get("version_fw") and enrichment["version_fw"] != device.version_fw:
            device.version_fw = enrichment["version_fw"]
            fields_changed.append("version_fw")
        if enrichment.get("status_device_field") and device.name == device.identity_name:
            device.name = enrichment["status_device_field"]
            fields_changed.append("name")
        # mac is derived deterministically from slug; only update if empty
        if enrichment.get("mac_address") and not device.mac_address:
            device.mac_address = enrichment["mac_address"]
            fields_changed.append("mac_address")
        # Build 156 item 7.5: parse Halo's serial from /api/status to set
        # the form-factor type. Webhook defaults to INDOOR; serial is the
        # firmware-baked authoritative source. Only overrides on first
        # enrichment so a user PATCH (Build 156 item 5) wins later.
        from alarm.enums import AlarmType
        type_from_serial = enrichment.get("type_from_serial")
        if type_from_serial and device.type == AlarmType.INDOOR:
            # Only "promote" from default INDOOR. If user PATCHed to OUTDOOR
            # already, OR firmware says INDOOR matching default, leave alone.
            if type_from_serial != device.type:
                device.type = type_from_serial
                fields_changed.append("type")
        if fields_changed:
            device.save(update_fields=fields_changed)
            logger.info(
                "halo_enriched id=%s fields=%s",
                device.id, fields_changed,
            )

        if not device.version_fw:
            # Halo HTTP server still unreachable — back off and retry.
            # Cap is max_retries=10 × 60s = 10min; after that the row
            # stays without fw_version and HA discovery uses what we have.
            try:
                raise self.retry()
            except self.MaxRetriesExceededError:
                logger.warning(
                    "halo_enrich_max_retries id=%s — giving up, HA discovery proceeds without fw_version",
                    device.id,
                )

    # Always publish HA discovery — republish-if-needed handles the dedup
    publish_ha_discovery_if_needed.delay(device.id)


@shared_task(queue="hub_operations_queue")
def publish_ha_discovery_if_needed(device_id: int):
    """Compute fingerprint of HA-relevant fields; republish only on change."""
    from alarm.models import AlarmDevice, HaDiscoveryState
    from utils.mqtt_client import MQTTClient

    try:
        device = AlarmDevice.objects.get(id=device_id)
    except AlarmDevice.DoesNotExist:
        return

    fingerprint = "|".join([
        device.mac_address or "",
        device.version_fw or "",
        str(device.ip_address or ""),
        device.name or "",
        device.type or "",
    ])

    state, _ = HaDiscoveryState.objects.get_or_create(device=device)
    if state.fingerprint == fingerprint:
        logger.debug("ha_discovery_no_change device=%s", device.identity_name)
        return

    payload = {
        "name":         device.name,
        "unique_id":    device.identity_name,
        "command_topic": f"/{device.identity_name}/mode",
        "state_topic":   f"/{device.identity_name}/status",
        "device": {
            "identifiers":  [device.identity_name],
            "manufacturer": "Jupyter",
            "model":        f"Halo-{device.type}",
            "sw_version":   device.version_fw,
        },
    }
    if device.mac_address:
        payload["device"]["connections"] = [["mac", device.mac_address]]

    topic = f"homeassistant/alarm_control_panel/{device.identity_name}/config"
    try:
        client = MQTTClient(
            host=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            username=settings.MQTT_USERNAME,
            password=settings.MQTT_PASSWORD,
        )
        client.connect()
        client.publish(topic, json.dumps(payload), retain=True)
        client.close()
    except Exception:
        logger.exception("ha_discovery_publish_failed device=%s", device.identity_name)
        return

    state.fingerprint = fingerprint
    state.save(update_fields=["fingerprint"])
    logger.info(
        "ha_discovery_published device=%s topic=%s",
        device.identity_name, topic,
    )


@shared_task
def alarm_unusual_sound_config(
    is_unusual_sound: bool, container_name: str, servicer_path
):
    camera_file_path = servicer_path

    # Match the chattr -i / +i dance used by camera_setting_config — the
    # AI bind-mount constants files are immutable between writes (set by
    # ota-lockdown.sh) so a bare open(..., "w") raises EPERM.
    subprocess.run(["chattr", "-i", camera_file_path], capture_output=True)

    try:
        with open(camera_file_path, "r", encoding="UTF-8") as file:
            lines = file.readlines()

        updated_lines = [
            (
                f"STOP_ALARM = {is_unusual_sound}\n"
                if line.strip().startswith("STOP_ALARM")
                else line
            )
            for line in lines
        ]

        with open(camera_file_path, "w", encoding="UTF-8") as file:
            file.writelines(updated_lines)
    finally:
        subprocess.run(["chattr", "+i", camera_file_path], capture_output=True)

    restart_service(container_name)
    return f"{container_name} restart config successfully."


@shared_task
def alarm_voice_ai_config(is_unusual_sound: bool, container_name: str):
    if is_unusual_sound is True:
        start_service(container_name)
    else:
        stop_service(container_name)

    restart_service(container_name)
    return f"{container_name} restart config successfully."


@shared_task
def monitor_alarm_ips():
    """
    Monitor alarm device (Halo) IP addresses AND hub IP changes.
    - If Halo unreachable: ARP sweep to find new IP, update DB.
    - If hub IP changed: push new hub IP to ALL reachable Halos via /audiosave.
    - Always sends /audiosave when either side changes so Halo NVS stays in sync.
    """
    import logging
    import os
    import requests
    from django.conf import settings
    from alarm.models import AlarmDevice
    from alarm.network import find_ip_by_mac, get_mac_address, ping_host

    devices = AlarmDevice.objects.all()
    if not devices.exists():
        return "No alarm devices"

    # Backfill IP from MQTT for devices that have no IP/MAC (onboard gap)
    # Uses EMQX HTTP Management API (not subprocess docker exec, which fails from gunicorn context)
    missing_ip = [d for d in devices if not d.ip_address]
    if missing_ip:
        try:
            emqx_password = os.environ.get("MQTT_PASSWORD", "")
            token_resp = requests.post(
                "http://localhost:18083/api/v5/login",
                json={"username": "admin", "password": emqx_password},
                timeout=5,
            )
            token_resp.raise_for_status()
            emqx_token = token_resp.json()["token"]

            clients_resp = requests.get(
                "http://localhost:18083/api/v5/clients?limit=100",
                headers={"Authorization": f"Bearer {emqx_token}"},
                timeout=5,
            )
            clients_resp.raise_for_status()
            mqtt_clients = {
                c["clientid"]: c["ip_address"]
                for c in clients_resp.json().get("data", [])
                if c.get("connected")
            }

            for device in missing_ip:
                identity = device.identity_name
                for clientid, client_ip in mqtt_clients.items():
                    if identity in clientid and client_ip and not client_ip.startswith("172."):
                        device.ip_address = client_ip
                        mac = get_mac_address(client_ip)
                        if mac:
                            device.mac_address = mac
                        update_fields = ["ip_address"]
                        if device.mac_address:
                            update_fields.append("mac_address")
                        device.save(update_fields=update_fields)
                        logging.info(f"Backfilled {identity} IP from MQTT API: {client_ip} mac={mac}")
                        break
        except Exception as e:
            logging.warning(f"MQTT API backfill failed: {e}")

    devices = AlarmDevice.objects.all()
    results = []
    hub_ip = None

    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        hub_ip = s.getsockname()[0]
        s.close()
    except Exception as e:
        logging.error(f"Failed to get hub IP: {e}")
        return "Failed to get hub IP"

    last_hub_ip_file = "/tmp/.jupyter_last_hub_ip"
    hub_ip_changed = False
    try:
        last_hub_ip = open(last_hub_ip_file).read().strip()
    except FileNotFoundError:
        last_hub_ip = ""
    if hub_ip != last_hub_ip:
        hub_ip_changed = True
        with open(last_hub_ip_file, "w") as f:
            f.write(hub_ip)
        if last_hub_ip:
            logging.info(f"Hub IP changed: {last_hub_ip} -> {hub_ip}")
            results.append(f"hub: IP changed {last_hub_ip} -> {hub_ip}")
        else:
            results.append(f"hub: IP recorded {hub_ip}")

    mqtt_port = getattr(settings, "MQTT_PORT", 5555)
    hub_slug = getattr(settings, "DEVICE_NAME", "")

    def push_audiosave(halo_ip, identity, reason):
        try:
            url = f"http://{halo_ip}/audiosave?local_ip={hub_ip}&port={mqtt_port}&hub_slug={hub_slug}"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                logging.info(f"Updated {identity} audio config ({reason})")
                return True
            else:
                logging.warning(f"Failed {identity} audio config ({reason}): {response.status_code}")
                return False
        except Exception as e:
            logging.error(f"Failed {identity} audio config ({reason}): {e}")
            return False

    for device in devices:
        identity = device.identity_name

        if not device.mac_address and device.ip_address:
            mac = get_mac_address(device.ip_address)
            if mac:
                device.mac_address = mac
                device.save(update_fields=["mac_address"])
                logging.info(f"Backfilled MAC for {identity}: {mac}")

        if device.ip_address and ping_host(device.ip_address):
            if hub_ip_changed:
                ok = push_audiosave(device.ip_address, identity, "hub IP changed")
                results.append(f"{identity}: OK, hub IP pushed={'yes' if ok else 'FAILED'}")
            else:
                results.append(f"{identity}: OK at {device.ip_address}")
            continue

        new_ip = None
        if device.mac_address:
            new_ip = find_ip_by_mac(device.mac_address, populate_arp=True)

        if new_ip and new_ip != device.ip_address:
            old_ip = device.ip_address
            device.ip_address = new_ip

            if not device.mac_address:
                mac = get_mac_address(new_ip)
                if mac:
                    device.mac_address = mac

            update_fields = ["ip_address"]
            if device.mac_address:
                update_fields.append("mac_address")
            device.save(update_fields=update_fields)

            ok = push_audiosave(new_ip, identity, f"moved {old_ip} -> {new_ip}")
            results.append(f"{identity}: moved {old_ip} -> {new_ip}, audio={'ok' if ok else 'FAILED'}")

        elif new_ip:
            if hub_ip_changed:
                push_audiosave(new_ip, identity, "hub IP changed")
            results.append(f"{identity}: recovered at {new_ip}")
        else:
            logging.warning(f"Alarm device {identity} unreachable")
            results.append(f"{identity}: OFFLINE")

    return "; ".join(results)
