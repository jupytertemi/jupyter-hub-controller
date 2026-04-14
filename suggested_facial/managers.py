from django.db import models


class SuggestedFacialManager(models.Manager):

    def create(self, **kwargs):
        facial = super().create(**kwargs)
        return facial
