import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend1.settings")

app = Celery("backend1")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Explicitly include task modules if autodiscovery misses newly added files in hot deploys.
app.conf.update(
	imports=(
		"api.tasks",
		"intruments.tasks",
	)
)

# Safety: ensure task names are registered early
try:
	from api.tasks import send_email_task  # noqa: F401
	from intruments import tasks as _intruments_tasks  # noqa: F401
except Exception as e:
	# Don't crash worker startup; just log to stderr
	import sys
	print(f"[CELERY WARN] Failed importing task modules early: {e}", file=sys.stderr)

# Optional periodic tasks
# Periodic reminder emails are currently disabled. To re-enable, restore the
# beat schedule below and ensure intruments.tasks.REMINDER_EMAILS_ENABLED is True.
# app.conf.beat_schedule = {
#     "send-reminder-every-minute": {
#         "task": "intruments.tasks.send_reminder_email",
#         "schedule": crontab(minute="*"),  # runs every minute
#     },
# }

