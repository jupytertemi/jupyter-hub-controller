import logging

from face_training.models import FaceTraining
from facial.models import Facial


def ignore_person(person_id: int, is_almost: bool):
    try:
        if not is_almost:
            Facial.objects.filter(id=person_id).update(is_ignore=True)
            FaceTraining.objects.filter(person_id=person_id).update(is_ignore=True)
            return None
        return None
    except Exception as e:
        logging.error(f"Error ignore person: {e}")
        return False


def update_person(person_id, is_almost, sugested_name):
    try:
        if not is_almost:
            Facial.objects.filter(id=person_id).update(name=sugested_name)
            FaceTraining.objects.filter(person_id=person_id).update(
                person_name=sugested_name
            )
            return None
        return None
    except Exception as e:
        logging.error(f"Error executing delete hub: {e}")
        return False
