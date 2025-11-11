# api/tasks.py

import logging
from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings

log = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, subject: str, body: str, to_email: str, html_message: str | None = None):
    """Generic email-sending task to avoid blocking request threads.
    Retries on transient failures.
    """
    try:
        # Email sending disabled via Celery (commented out intentionally)
        # send_mail(
        #     subject=subject,
        #     message=body,
        #     from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        #     recipient_list=[to_email],
        #     html_message=html_message,
        #     fail_silently=False,
        # )
        # log.info("Email sent to %s: %s", to_email, subject)
        pass
    except Exception as exc:
        log.error("Email send failed to %s: %s", to_email, exc)
        raise self.retry(exc=exc)
