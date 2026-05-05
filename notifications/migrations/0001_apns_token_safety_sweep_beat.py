"""Register the daily APNs token safety-sweep on Celery beat.

Runs notifications.tasks.cleanup_unused_apns_tokens at 04:30 daily,
removing tokens whose last_seen_at is older than 30 days.

Pattern matches hub_operations/migrations/0001_create_cloudflare_restart_task.py —
each app registers its own beat schedule via a data migration so a fresh
hub onboard automatically has all scheduled jobs configured without an
extra mgmt command step.
"""
from django.db import migrations, transaction


def create_safety_sweep_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="30",
        hour="4",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone="UTC",
    )

    with transaction.atomic():
        PeriodicTask.objects.get_or_create(
            name="Notifications | Daily APNs token safety-sweep",
            task="notifications.tasks.cleanup_unused_apns_tokens",
            crontab=schedule,
            defaults={
                "enabled": True,
                "description": (
                    "Remove APNs tokens not refreshed in 30 days. "
                    "Safety net for uninstalls that never triggered an "
                    "in-band 410 cleanup. Idempotent."
                ),
            },
        )


def reverse_safety_sweep_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name="Notifications | Daily APNs token safety-sweep"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("django_celery_beat", "0016_alter_crontabschedule_timezone")]

    operations = [
        migrations.RunPython(
            create_safety_sweep_task,
            reverse_safety_sweep_task,
        ),
    ]
