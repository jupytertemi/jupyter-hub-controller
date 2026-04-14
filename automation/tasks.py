import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings

from utils.hass_client import HassClient
from utils.restarting_service import restart_service


@shared_task
def automation_alarm_loitering_config(
    is_loitering: bool, container_name: str, servicer_path
):
    camera_file_path = servicer_path
    with open(camera_file_path, "r", encoding="UTF-8") as file:
        lines = file.readlines()

    updated_lines = [
        (
            f"IS_LOITERING = {is_loitering}\n"
            if line.strip().startswith("IS_LOITERING")
            else line
        )
        for line in lines
    ]

    # Write and update the file
    with open(camera_file_path, "w", encoding="UTF-8") as file:
        file.writelines(updated_lines)

    restart_service(container_name)
    return f"{container_name} restart config successfully."


@shared_task(bind=True, max_retries=4)
def create_manual_alarm_automations(self, entry_id):
    try:
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()

        result = client.add_allow_service_esphome(entry_id)

        logging.info(
            f"[create_manual_alarm_automations] "
            f"entry_id={entry_id}, result={result}"
        )
    except MaxRetriesExceededError:
        logging.error(f"[ESPHome] FINAL FAIL → push to fallback | entry_id={entry_id}")

        # Push to dead-letter / fallback
        retry_esphome_later.delay(entry_id)
        raise
    except Exception as exc:
        retries = self.request.retries
        logging.warning(
            f"[create_manual_alarm_automations] "
            f"entry_id={entry_id}, retry={retries}, error={exc}"
        )

        if retries < 3:
            raise self.retry(exc=exc, countdown=5)

        raise self.retry(exc=exc, countdown=300)


@shared_task(bind=True, max_retries=6, default_retry_delay=3600)
def retry_esphome_later(self, entry_id):
    """
    Retry dead mail:
    - Retry every 1 hour
    - Maximum 6 attempts (~6 hours)
    - No mainstream image
    """
    try:
        logging.info(
            f"[ESPHome-Fallback] retry={self.request.retries} | entry_id={entry_id}"
        )

        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()
        client.add_allow_service_esphome(entry_id)

        logging.info(f"[ESPHome-Fallback] SUCCESS | entry_id={entry_id}")

    except Exception as exc:
        logging.warning(
            f"[ESPHome-Fallback] FAILED | entry_id={entry_id} | error={exc}"
        )
        raise self.retry(exc=exc)
