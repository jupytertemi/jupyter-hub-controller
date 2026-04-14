from django.db import models
from django.db.models import Count

from event.enums import LabelType


class EventManager(models.Manager):
    def counts(self, queryset):
        totals = {label: 0 for label, _ in LabelType.choices}
        for row in queryset.values("label").annotate(total=Count("id")):
            totals[row["label"]] = row["total"]
        return totals
