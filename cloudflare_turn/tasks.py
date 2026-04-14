import logging
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from cloudflare_turn.models import Turn
from utils.api import APIClient
from utils.restarting_service import restart_system_service
from utils.update_env import read_env_file


def render_and_write_config(template_name, context, output_path):
    config = render_to_string(template_name, context)
    with open(output_path, "w", encoding="UTF-8") as config_file:
        config_file.write(config)


def get_cameras():
    model = apps.get_model("camera.camera")
    cameras = model.objects.all()
    camera_data = []
    for camera in cameras:
        camera_data.append(
            {
                "name": camera.slug_name,
                "rtsp_url": camera.rtsp_url,
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


def update_mediamtx_config(ice_response):
    try:
        camera_data = get_cameras()
        logging.info(f"Camera Data: {camera_data}")
        logging.info(f"ICE Server Data: {ice_response}")
        logging.info(f"ICE MEDIAMTX_CONFIG_PATH: {settings.MEDIAMTX_CONFIG_PATH}")

        ice_server = ice_response.get("previous_turn")
        if not ice_server:
            logging.error("No previous_turn data")
            ice_server = ice_response
        else:
            created_at_str = ice_server.get("created_at")
            if not created_at_str:
                logging.error("previous_turn has no created_at")
                return

            created_at = parse_datetime(created_at_str)
            if not created_at:
                logging.error(f"Invalid created_at format: {created_at_str}")
                return

            now = timezone.now()
            expire_time = created_at + timedelta(days=2)
            remaining_time = expire_time - now

            if remaining_time < timedelta(hours=24):
                logging.info(f"Use new turn sever: {ice_response}")
                ice_server = ice_response

            logging.info(f"TURN credential valid. Remaining time: {remaining_time}")

        ice_credential = ice_server.get("credential")

        if not ice_credential or len(ice_credential) < 2:
            logging.error("Invalid ICE server data")
            return

        stun_server = ice_credential[0]["urls"]
        turn_server = ice_credential[1]["urls"]
        turn_user = ice_credential[1]["username"]
        turn_password = ice_credential[1]["credential"]

        render_and_write_config(
            "mediamtx.yml",
            {
                "cameras": camera_data,
                "stun_server": stun_server,
                "turn_server": turn_server,
                "turn_user": turn_user,
                "turn_password": turn_password,
            },
            settings.MEDIAMTX_CONFIG_PATH,
        )
        restart_system_service("mediamtx")
    except Exception as e:
        logging.error(f"Error executing update mediamtx config: {e}")
        return


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


def hub_auto_restart_cloudflare_token():
    logging.info("Hub auto-restart cloudflare turn")
    response = get_ice_server()

    if response == {}:
        logging.error("Get invalid ice data")
        return

    uid = response.get("uid")
    name = response.get("name")
    credential = response.get("credential")
    previous_turn_data = response.get("previous_turn")

    # Update if it already exists, create if it doesn't.
    turn_obj, created = Turn.objects.update_or_create(
        uid=uid,
        defaults={
            "name": name,
            "credential": credential,
            "previous_turn": previous_turn_data,
        },
    )

    if created:
        logging.info(f"Created TURN: {turn_obj}")
    else:
        logging.info(f"Updated TURN: {turn_obj}")

    try:
        # Update MediaMTX config
        update_mediamtx_config(response)
        return "Camera config updated successfully."
    except Exception as e:
        return f"Error updating camera config: {str(e)}"
