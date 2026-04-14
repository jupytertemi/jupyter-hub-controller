from django.conf import settings
from django.db import models

from facial.tasks import create_facial_config
from utils.upload_file import UploadFileHandler


class FacialManager(models.Manager):

    def create(self, **kwargs):
        video_file = kwargs.pop("video_file")
        avatar_file = kwargs.pop("avatar_file")
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

        data = {
            "uuid": facial.id,
            "name": facial.name,
            "video_url": facial.video_url,
        }
        create_facial_config.apply_async(args=(data,), queue="facial_queue")
        return facial
