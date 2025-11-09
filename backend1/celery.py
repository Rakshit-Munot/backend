import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend1.settings")

app = Celery("backend1")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Optional periodic tasks
# Periodic reminder emails are currently disabled. To re-enable, restore the
# beat schedule below and ensure intruments.tasks.REMINDER_EMAILS_ENABLED is True.
# app.conf.beat_schedule = {
#     "send-reminder-every-minute": {
#         "task": "intruments.tasks.send_reminder_email",
#         "schedule": crontab(minute="*"),  # runs every minute
#     },
# }

