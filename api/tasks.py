# api/tasks.py

import logging
from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.core.cache import cache as _dj_cache
from typing import Optional, List
from django.db.models.functions import ExtractYear
from django.utils import timezone

from .models import Bill, Handout, Lab

log = logging.getLogger(__name__)

def _emails_allowed() -> bool:
    """Emails should only send when DEBUG is True as per requirement."""
    return bool(getattr(settings, "DEBUG", True))


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, subject: str, body: str, to_email: str, html_message: str | None = None):
    """Generic email-sending task to avoid blocking request threads.
    Retries on transient failures.
    """
    try:
        if not _emails_allowed():
            log.info("Email suppressed (DEBUG=False) to %s: %s", to_email, subject)
            return
        # Send email synchronously inside Celery worker
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
            recipient_list=[to_email],
            html_message=html_message,
            fail_silently=False,
        )
        log.info("Email sent to %s: %s", to_email, subject)
    except Exception as exc:
        log.error("Email send failed to %s: %s", to_email, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True)
def warm_bills_handouts_cache(self) -> None:
    """
    Pre-warm caches used by Bills, Labs, and Handouts listings for faster first loads.
    Warms first page for defaults and years/labs lists.
    """
    try:
        # Bills: first page without FY filter + years list
        # Keys mirror api.py helpers
        from django.core.cache import cache as dj_cache
        def _bills_cache_key(fy: Optional[str], page: int, limit: int) -> str:
            ver = dj_cache.get("bills:cache:version") or 1
            return f"bills:v{ver}:fy={fy or 'ALL'}:p={page}:l={limit}"

        def _bills_years_cache_key() -> str:
            ver = dj_cache.get("bills:cache:version") or 1
            return f"bills:v{ver}:years"

        # Build bills page 1
        page, limit = 1, 10
        qs = Bill.objects.only(
            "id", "bill_no", "amount", "file_url", "original_filename", "public_id", "resource_type", "comment", "uploaded_at", "financial_year"
        ).order_by("-uploaded_at")
        total = qs.count()
        items_qs = qs[:limit]
        total_pages = (total + limit - 1) // limit
        items = [
            {
                "id": b.id,
                "bill_no": b.bill_no,
                "amount": float(b.amount),
                "file_url": b.file_url,
                "original_filename": b.original_filename,
                "public_id": b.public_id,
                "resource_type": b.resource_type,
                "comment": getattr(b, "comment", None),
                "uploaded_at": b.uploaded_at,
                "financial_year": b.financial_year,
            }
            for b in items_qs
        ]
        bills_result = {"items": items, "page": page, "total_pages": total_pages, "total": total}
        dj_cache.set(_bills_cache_key(None, page, limit), bills_result, 86400)

        # Years
        years = (
            Bill.objects.values_list("financial_year", flat=True).distinct().order_by("-financial_year")
        )
        dj_cache.set(_bills_years_cache_key(), list(years), 86400)

        # Labs list
        def _labs_cache_key() -> str:
            ver = dj_cache.get("labs:cache:version") or 1
            return f"labs:v{ver}:all"

        labs = list(Lab.objects.all().order_by("name"))
        dj_cache.set(_labs_cache_key(), labs, 300)

        # Handouts: first page all + per-lab first page
        def _handouts_cache_key(lab_id: Optional[int], page: int, limit: int, q: Optional[str]) -> str:
            ver = dj_cache.get("handouts:cache:version") or 1
            lab_part = f"lab={lab_id if lab_id is not None else 'ALL'}"
            q_part = f"q={(q or '').strip().lower()}"
            return f"handouts:v{ver}:{lab_part}:{q_part}:p={page}:l={limit}"

        page, limit = 1, 10
        base_qs = Handout.objects.all().order_by("-uploaded_at")
        total = base_qs.count()
        items_qs = base_qs[:limit]
        items = [
            {
                "id": h.id,
                "title": h.title,
                "description": h.description,
                "comment": getattr(h, "comment", None),
                "file_url": h.file_url,
                "original_filename": h.original_filename,
                "uploaded_at": h.uploaded_at,
            }
            for h in items_qs
        ]
        dj_cache.set(_handouts_cache_key(None, page, limit, ""), {"items": items, "page": page, "total_pages": (total + limit - 1) // limit, "total": total}, 300)

        # Handout years
        years = (
            Handout.objects.annotate(y=ExtractYear("uploaded_at")).values_list("y", flat=True).distinct().order_by("-y")
        )
        dj_cache.set(f"handouts:v{dj_cache.get('handouts:cache:version') or 1}:years", [int(y) for y in years if y], 600)

        # Per-lab first page
        for lab in labs:
            qs = Handout.objects.filter(lab_id=lab.id).order_by("-uploaded_at")
            total = qs.count()
            items_qs = qs[:limit]
            items = [
                {
                    "id": h.id,
                    "title": h.title,
                    "description": h.description,
                    "comment": getattr(h, "comment", None),
                    "file_url": h.file_url,
                    "original_filename": h.original_filename,
                    "uploaded_at": h.uploaded_at,
                }
                for h in items_qs
            ]
            dj_cache.set(_handouts_cache_key(lab.id, page, limit, ""), {"items": items, "page": page, "total_pages": (total + limit - 1) // limit, "total": total}, 300)

        log.info("[Task %s] Warmed bills/handouts caches: bills_total=%s labs=%s", getattr(self, 'request', None) and self.request.id, total, len(labs))
    except Exception as e:
        log.exception("warm_bills_handouts_cache failed: %s", e)
