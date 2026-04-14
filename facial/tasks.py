import json
import logging
import os
import shutil
import time

from celery import shared_task
from django.apps import apps
from django.conf import settings

from utils.restarting_service import restart_service


def remove_traing_data():
    # clear training data
    try:
        # remove file result
        if os.path.isfile(settings.TRAINING_RESULT_FILE):
            with open(settings.TRAINING_RESULT_FILE, "w"):
                pass

        # make training video path empty
        if os.path.exists(settings.TRAINING_FOLDER_PATH):
            for file in os.listdir(settings.TRAINING_FOLDER_PATH):
                file_path = os.path.join(settings.TRAINING_FOLDER_PATH, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
        else:
            os.makedirs(settings.TRAINING_FOLDER_PATH)

        person_data = {"TRAINING_PERSON_NAME": "", "TRAINING_PERSON_ID": ""}

        with open(settings.PERSON_DATA_PATH, "w") as json_file:
            json.dump(person_data, json_file, indent=4)

    except Exception as e:
        logging.error(f"Remove training data error: {e}")


def apply_training_video(training_video):
    try:
        # move training video to process folder
        if os.path.exists(training_video):
            destination_file = os.path.join(
                settings.TRAINING_FOLDER_PATH, "training_video.mp4"
            )
            shutil.move(training_video, destination_file)
            os.chown(destination_file, 1000, 1000)
        else:
            logging.error("Training video not exist")
            return False
        return True
    except Exception as e:
        logging.error(f"Apply training video error: {e}")
        return False


@shared_task
def create_facial_config(model: dict):
    """Handle create facial ai"""
    logging.info(f"path_video: {model}")
    # remove training data
    remove_traing_data()
    # start training
    video_name = model.get("video_url", "")
    start_train_result = apply_training_video(
        training_video=f"{settings.RECEIVING_FILE_DIR}/{video_name}"
    )
    if not start_train_result:
        return "Facial created fail."
    # set training person name

    person_data = {
        "TRAINING_PERSON_NAME": model.get("name"),
        "TRAINING_PERSON_ID": str(model.get("uuid")),
    }

    with open(settings.PERSON_DATA_PATH, "w") as json_file:
        json.dump(person_data, json_file, indent=4)

    # start training process
    restart_service(settings.FACE_TRAINING_CONTAINER_NAME)

    # wait training done
    traing_result = None
    while True:
        if os.path.isfile(settings.TRAINING_RESULT_FILE):
            with open(settings.TRAINING_RESULT_FILE, "r") as file:
                traing_result = file.readline().replace("\n", "").strip()
                if traing_result in ["Exist", "Fail", "Success"]:
                    break
        time.sleep(3)
    processing = traing_result
    # update status process training
    facial_model = apps.get_model("facial.facial")
    facil_object = facial_model.objects.get(id=model.get("uuid", ""))
    facil_object.processing = processing
    facil_object.save()
    # clear data
    remove_traing_data()

    return "Facial created done."
