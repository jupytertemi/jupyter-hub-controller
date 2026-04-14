from django.db import models


class BoundingBox(models.Model):
    zone_type = models.CharField(max_length=256)
    camera_id = models.CharField(max_length=256)
    x1 = models.IntegerField()
    y1 = models.IntegerField()
    x2 = models.IntegerField()
    y2 = models.IntegerField()
