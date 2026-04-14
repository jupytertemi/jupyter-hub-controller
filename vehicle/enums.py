from django.db import models
from django.utils.translation import gettext_lazy as _


class CategoryType(models.TextChoices):
    FAMILY = "FAMILY", _("FAMILY Vehicle")
    FRIEND = "FRIEND", _("Ring Vehicle")
    __empty__ = _("None")
