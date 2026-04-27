import logging
import os

from django.conf import settings
from django.db import models

from facial.tasks import create_facial_config
from utils.upload_file import UploadFileHandler


class FacialManager(models.Manager):

    def create(self, **kwargs):
        video_file = kwargs.pop("video_file")
        avatar_file = kwargs.pop("avatar_file")
        frame_files = kwargs.pop("frame_files", [])
        video_filename = UploadFileHandler(
            video_file, settings.BASE_DIR_VIDEO
        ).save_file()
        avatar_filename = UploadFileHandler(
            avatar_file, settings.BASE_DIR_FILE
        ).save_file()
        avatar_url = f"/api/face-avatar/{avatar_filename}"

        kwargs.setdefault("video_url", video_filename)
        kwargs.setdefault("avatar", avatar_url)
        facial = super().create(**kwargs)

        frame_count = 0
        if frame_files:
            staging_dir = os.path.join(os.path.dirname(settings.TRAINING_FOLDER_PATH), "validated_frames_staging")
            os.makedirs(staging_dir, exist_ok=True)
            for i, f in enumerate(frame_files):
                dest = os.path.join(staging_dir, f"frame_{i:03d}.jpg")
                with open(dest, "wb") as out:
                    for chunk in f.chunks():
                        out.write(chunk)
                try:
                    os.chown(dest, 1000, 1000)
                except OSError:
                    pass
                frame_count += 1
            logging.info(f"Staged {frame_count} validated frames for {facial.name}")

        data = {
            "uuid": facial.id,
            "name": facial.name,
            "video_url": facial.video_url,
            "has_validated_frames": frame_count > 0,
            "validated_frame_count": frame_count,
        }
        create_facial_config.apply_async(args=(data,), queue="facial_queue")
        return facial
