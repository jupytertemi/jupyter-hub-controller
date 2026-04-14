import logging

import requests
from django.conf import settings

from utils.token_generate import generate_basic_token
from utils.update_env import read_env_file


def delete_hub_request():
    try:
        headers = {
            "Content-type": "application/json",
            "Authorization": generate_basic_token(
                username=read_env_file("DEVICE_NAME"),
                password=read_env_file("HUB_SECRET"),
            ),
        }
        params = {}
        r = requests.delete(
            url=settings.JUPYTER_HOST + settings.HUB_DELETE_URL,
            headers=headers,
            params=params,
        )
        logging.info(f"Delete hub request status {r.status_code}")
        if r.status_code == 204:
            return True
        logging.error(f"Delete hub fail response {r}")
        logging.error("Delete hub fail")
        return False
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Error executing delete hub: {e}")
        return False
    except Exception as e:
        logging.error(f"Error executing delete hub: {e}")
        return False
