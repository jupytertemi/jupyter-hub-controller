from celery import shared_task
from django.conf import settings

from utils.restarting_service import restart_service


@shared_task
def update_loitering_config(restrictive_zone: bool):
    file_path = settings.LOITERING_CONFIG_PATH
    with open(file_path, "r", encoding="UTF-8") as file:
        lines = file.readlines()

    updated_lines = [
        (
            f"USE_RESTRICTIVE_ZONE = {restrictive_zone}\n"
            if line.strip().startswith("USE_RESTRICTIVE_ZONE")
            else line
        )
        for line in lines
    ]

    # Write and update the file
    with open(file_path, "w", encoding="UTF-8") as file:
        file.writelines(updated_lines)

    container_name = settings.LOITERING_CONTAINER_NAME
    restart_service(container_name)
    return "Loitering config updated successfully."
