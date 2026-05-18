import logging
import subprocess
from datetime import datetime

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.utils import timezone

from utils.hass_client import HassClient
from utils.restarting_service import restart_service

log = logging.getLogger(__name__)

_last_schedule_action = {}


@shared_task
def check_alarm_schedules():
    from automation.enums import AlarmScheduleRepeatType, AlarmSettingsMode
    from automation.models import AlarmSettings

    now = timezone.localtime()
    current_weekday = now.strftime("%A")
    current_minutes = now.hour * 60 + now.minute

    scheduled = AlarmSettings.objects.filter(schedule=True).select_related("device")
    for s in scheduled:
        if not _should_run_today(s, current_weekday):
            continue

        start_minutes = _epoch_to_minutes(s.schedule_start)
        end_minutes = _epoch_to_minutes(s.schedule_end)

        if start_minutes is None or end_minutes is None:
            continue

        cache_key = f"alarm_{s.pk}"

        if current_minutes == start_minutes:
            if _last_schedule_action.get(cache_key) == ("arm", current_minutes):
                continue
            if s.mode == AlarmSettingsMode.NONE.value:
                target_mode = AlarmSettingsMode.AWAY.value
            else:
                target_mode = s.mode
            log.info(
                "schedule arm: alarm=%s mode=%s at %02d:%02d",
                s.pk, target_mode, now.hour, now.minute,
            )
            AlarmSettings.objects.update_instance(s, mode=target_mode)
            _last_schedule_action[cache_key] = ("arm", current_minutes)

        elif current_minutes == end_minutes:
            if _last_schedule_action.get(cache_key) == ("disarm", current_minutes):
                continue
            log.info(
                "schedule disarm: alarm=%s at %02d:%02d",
                s.pk, now.hour, now.minute,
            )
            AlarmSettings.objects.update_instance(
                s, mode=AlarmSettingsMode.NONE.value
            )
            _last_schedule_action[cache_key] = ("disarm", current_minutes)


def _epoch_to_minutes(epoch_seconds):
    if not epoch_seconds:
        return None
    try:
        dt = datetime.fromtimestamp(epoch_seconds, tz=timezone.get_current_timezone())
        return dt.hour * 60 + dt.minute
    except (OSError, ValueError, OverflowError):
        return None


def _should_run_today(settings_obj, current_weekday):
    from automation.enums import AlarmScheduleRepeatType

    rt = settings_obj.repeat_type
    if rt == AlarmScheduleRepeatType.EVERY_DAY.value:
        return True
    if rt == AlarmScheduleRepeatType.CUSTOM.value:
        return current_weekday in (settings_obj.schedule_repeat or [])
    if rt == AlarmScheduleRepeatType.NEVER.value:
        return True
    return True


@shared_task
def automation_alarm_loitering_config(
    is_loitering: bool, container_name: str, servicer_path
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
                f"IS_LOITERING = {is_loitering}\n"
                if line.strip().startswith("IS_LOITERING")
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
