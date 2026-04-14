import logging
import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub_controller.settings.production")

app = Celery("hub_controller.settings")
app.config_from_object("django.conf:settings", namespace="CELERY")

app.conf.broker_heartbeat = 600
app.conf.task_acks_late = True
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    logging.info(f"Request: {self.request!r}")
