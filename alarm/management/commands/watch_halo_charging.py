"""
MQTT subscriber that watches Halo charging state and relays changes
to the jupyter cloud, which sends APNs push-to-start Live Activities.

Run as: python manage.py watch_halo_charging
Deploy as a systemd service alongside the hub-controller.
"""
import json
import logging
import os
import threading
import time

import paho.mqtt.client as mqtt
import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from alarm.models import AlarmDevice
from utils.token_generate import generate_basic_token

logger = logging.getLogger("halo_charging_watcher")


class Command(BaseCommand):
    help = "Watch Halo MQTT status and relay charging state to cloud"

    def handle(self, *args, **options):
        env_path = "/root/jupyter-container/.env"
        env_vars = _load_env(env_path)

        slug_name = env_vars.get("DEVICE_NAME", "")
        hub_secret = env_vars.get("DEVICE_SECRET", "")
        cloud_url = settings.JUPYTER_HOST

        if not slug_name or not hub_secret:
            logger.error("DEVICE_NAME or DEVICE_SECRET not set, exiting")
            return

        mqtt_host = getattr(settings, "MQTT_HOST", "localhost")
        mqtt_port = int(getattr(settings, "MQTT_PORT", 1883))
        mqtt_user = getattr(settings, "MQTT_USERNAME", None)
        mqtt_pass = getattr(settings, "MQTT_PASSWORD", None)

        charging_state = {}

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                logger.info("Connected to MQTT broker")
                devices = AlarmDevice.objects.all()
                for device in devices:
                    topic = f"/{device.identity_name}/status"
                    client.subscribe(topic)
                    logger.info(f"Subscribed to {topic}")
                if not devices.exists():
                    client.subscribe("+/status")
                    logger.info("No devices found, subscribed to +/status")
            else:
                logger.error(f"MQTT connect failed rc={rc}")

        def on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode())
                is_charging = payload.get("charging", False)
                halo_name = payload.get("device", "Halo")
                topic = msg.topic
                identity = topic.strip("/").split("/")[0]

                prev = charging_state.get(identity)
                charging_state[identity] = is_charging

                if prev is None:
                    if not is_charging:
                        return

                if prev == is_charging:
                    return

                logger.info(
                    f"Charging state change: {identity} {prev} -> {is_charging}"
                )

                device = AlarmDevice.objects.filter(
                    identity_name=identity
                ).first()
                display_name = device.name if device else halo_name

                _relay_to_cloud(
                    cloud_url=cloud_url,
                    slug_name=slug_name,
                    hub_secret=hub_secret,
                    halo_name=display_name,
                    is_charging=is_charging,
                    charge_percent=int(payload.get("battery_percent", 0)),
                    charge_time_min=int(payload.get("charge_time_minutes", 0)),
                    wifi_quality=_wifi_quality(payload.get("wifi_rssi")),
                    temperature=float(payload.get("temperature", 0)),
                )
            except Exception:
                logger.exception("Error processing MQTT message")

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="halo-charging-watcher",
        )
        if mqtt_user and mqtt_pass:
            client.username_pw_set(mqtt_user, mqtt_pass)

        client.on_connect = on_connect
        client.on_message = on_message

        while True:
            try:
                logger.info(f"Connecting to MQTT {mqtt_host}:{mqtt_port}")
                client.connect(mqtt_host, mqtt_port, keepalive=60)
                client.loop_forever()
            except Exception:
                logger.exception("MQTT connection lost, retrying in 10s")
                time.sleep(10)


def _relay_to_cloud(
    cloud_url, slug_name, hub_secret,
    halo_name, is_charging, charge_percent,
    charge_time_min, wifi_quality, temperature,
):
    url = f"{cloud_url}/halo-charging-state"
    headers = {
        "Content-Type": "application/json",
        "Authorization": generate_basic_token(
            username=slug_name, password=hub_secret
        ),
    }
    payload = {
        "halo_name": halo_name,
        "is_charging": is_charging,
        "charge_percent": charge_percent,
        "charge_time_remaining_min": charge_time_min,
        "wifi_signal_quality": wifi_quality,
        "temperature_c": temperature,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        logger.info(
            f"Cloud relay: {resp.status_code} sent={resp.json().get('sent', 0)}"
        )
    except Exception:
        logger.exception("Failed to relay charging state to cloud")


def _wifi_quality(rssi):
    if rssi is None:
        return "Unknown"
    rssi = float(rssi)
    if rssi >= -50:
        return "Excellent"
    if rssi >= -60:
        return "Good"
    if rssi >= -70:
        return "Average"
    return "Poor"


def _load_env(path):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("'\"")
    return env
