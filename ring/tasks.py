import hashlib
import json
import os
import secrets

from celery import shared_task
from django.conf import settings


def read_json_file(path):
    """Read JSON file, return dict or empty dict if missing/invalid."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def write_json_file(path, data):
    """Write dict to JSON file with indentation."""
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


@shared_task
def clear_ring_auth():
    """
    Remove Ring auth from state file, config file, and database.
    Called when the last Ring camera is deleted to prevent orphaned tokens
    blocking new Ring doorbell onboarding.
    """
    from ring.models import RingAccount

    # Clear ring-state.json (contains refresh_token + device configs)
    state_file = settings.RING_STREAM_CONFIG_STATE_PATH
    if os.path.exists(state_file):
        write_json_file(state_file, {})

    # Clear config.json (contains MQTT credentials)
    config_file = settings.RING_STREAM_CONFIG_PATH
    if os.path.exists(config_file):
        write_json_file(config_file, {})

    # Clear go2rtc.yaml (contains stream definitions for deleted devices)
    go2rtc_path = os.path.join(os.path.dirname(state_file), "go2rtc.yaml")
    if os.path.exists(go2rtc_path):
        try:
            os.remove(go2rtc_path)
        except OSError:
            pass

    # Delete all RingAccount records from database
    RingAccount.objects.all().delete()

    return "Ring auth cleared successfully."


@shared_task
def set_ring_token(refresh_token):
    """
    Update the RING token in the ring-state file and update MQTT in config file.
    """
    try:
        # --- Update state file ---
        state_file = settings.RING_STREAM_CONFIG_STATE_PATH
        state_data = read_json_file(state_file)

        if not state_data.get("systemId"):
            random_bytes = secrets.token_bytes(32)
            state_data["systemId"] = hashlib.sha256(random_bytes).hexdigest()

        state_data["ring_token"] = refresh_token
        write_json_file(state_file, state_data)

        # --- Update config file ---
        config_file = settings.RING_STREAM_CONFIG_PATH
        config_data = read_json_file(config_file)

        config_data["mqtt_url"] = (
            f"mqtt://ring_camera:{settings.MQTT_RING_CAMERA_PASSWORD}@emqx:1883"
        )
        write_json_file(config_file, config_data)

    except Exception as e:
        return f"Failed to update ring token: {e}"

    return f"{settings.RING_STREAM_CONTAINER} config updated successfully."
