from celery import shared_task
from django.conf import settings
from django.template.loader import render_to_string

from utils.restarting_service import restart_service


@shared_task
def update_parcel_detect_config(data):
    camera = data.get("camera_name", None)
    box = data.get("box", [])
    if camera:
        camera_file_path = settings.PARCEL_CONFIG_PATH
        with open(camera_file_path, "r", encoding="UTF-8") as file:
            lines = file.readlines()

        updated_lines = [
            (
                f"CAMERA_NAME = {camera}\n"
                if line.strip().startswith("CAMERA_NAME")
                else line
            )
            for line in lines
        ]

        # Write and update the file
        with open(camera_file_path, "w", encoding="UTF-8") as file:
            file.writelines(updated_lines)

    if box:
        context = {"box": box}
        config = render_to_string("box.yaml", context)
        with open(
            settings.PARCEL_BOX_CONFIG_PATH, "w", encoding="UTF-8"
        ) as config_file:
            config_file.write(config)

    container_name = settings.PARCEL_CONTAINER_NAME
    restart_service(container_name)
    return "Parcel Detect config updated successfully."
