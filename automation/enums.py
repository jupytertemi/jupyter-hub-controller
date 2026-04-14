from django.db import models
from django.utils.translation import gettext_lazy as _


class AlarmSettingsMode(models.TextChoices):
    NONE = "none", _("None")
    TRAVEL = "travel", _("Travel")
    NIGHT = "night", _("Night")
    AWAY = "away", _("Away")


class AlarmSound(models.TextChoices):
    ALARM = "alarm", _("Alarm")
    PEOPLE_HOME = "people_home", _("People at Home")
    RUNNING_APPLIANCES = "running_appliances", _("Running Appliances")
    BARKING_DOGS = "barking_dogs", _("Barking Dogs")


class AlarmScheduleRepeatType(models.TextChoices):
    EVERY_DAY = "every_day", _("Every Day")
    CUSTOM = "custom", _("Custom")
    NEVER = "never", _("Never")


class Weekdays(models.TextChoices):
    MONDAY = "Monday", _("Monday")
    TUESDAY = "Tuesday", _("Tuesday")
    WEDNESDAY = "Wednesday", _("Wednesday")
    THURSDAY = "Thursday", _("Thursday")
    FRIDAY = "Friday", _("Friday")
    SATURDAY = "Saturday", _("Saturday")
    SUNDAY = "Sunday", _("Sunday")


class AlarmTriggerConditions(models.TextChoices):
    TRUSTED_FACE_DISARM = (
        "{{ trigger.id == 'entry_door_opened' or trigger.payload_json.person_id == none }}"
    )
    NOT_TRUSTED_FACE_DISARM = "{{trigger.payload_json.person_id != none }}"
    FACE_CONFIDENCE_ABOVE_THRESHOLD = (
        "{{ trigger.payload_json.confidence_score | float(0) > 0.5 }}"
    )


class AlarmDetectedSound(models.TextChoices):
    ALL_SOUND = "all_sound", _("All Sound")
    DANGEROUS = "dangerous", _("Dangerous")
    NOTEWORTHY = "noteworthy", _("Noteworthy")
