# intruments/tasks.py

from typing import Optional, Dict, Any
from datetime import timedelta, datetime
import logging

from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.conf import settings
from zoneinfo import ZoneInfo
from collections import defaultdict
from django.utils.html import escape

from .models import IssueRequest, Item, Category, SubCategory, IssueMessage

logger = logging.getLogger(__name__)


# ---------------------------
# Helpers
# ---------------------------
IST = ZoneInfo("Asia/Kolkata")
MESSAGES_CACHE_TIMEOUT = 300
MESSAGES_CACHE_MAX = 200

# Feature flags / toggles
# Set to False to temporarily disable reminder emails without removing code paths.
REMINDER_EMAILS_ENABLED: bool = False

def _fmt_ist(dt) -> str:
    """Format datetime in IST with a clean, human-friendly format."""
    if not dt:
        return ""
    try:
        aware = timezone.localtime(dt, IST) if timezone.is_aware(dt) else timezone.make_aware(dt, IST)
    except Exception:
        try:
            aware = timezone.make_aware(dt, IST)
        except Exception:
            aware = dt
    return aware.strftime("%d %b %Y, %I:%M %p IST")


def _wrap_html_email(heading: str, inner_html: str) -> str:
        """Wrap inner HTML with a simple, professional layout."""
        heading = escape(heading)
        return f"""
        <div style="font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; background: #f7f7fb; padding: 24px;">
            <div style="max-width:640px; margin:0 auto; background:#ffffff; border:1px solid #e5e7eb; border-radius: 12px; overflow:hidden;">
                <div style="background:linear-gradient(90deg,#6d28d9,#2563eb); padding:16px 20px; color:#fff;">
                    <h2 style="margin:0; font-size:18px; font-weight:600;">{heading}</h2>
                </div>
                <div style="padding:20px; color:#111827; font-size:14px; line-height:1.6;">
                    {inner_html}
                    <p style="margin-top:16px; color:#6b7280;">Regards,<br/>Lab Team</p>
                </div>
            </div>
            <p style="text-align:center; margin-top:12px; color:#9ca3af; font-size:12px;">This is an automated message. Please do not reply directly to this email.</p>
        </div>
        """


# ---------------------------
# Email reminders
# ---------------------------
@shared_task(bind=True)
def send_reminder_email(self, request_id: int, when: str):
    """
    Sends a reminder email to the user about returning an instrument.
    'when' is a string describing the reminder, e.g., "1 day before" or "1 hour before".
    """
    try:
        req = IssueRequest.objects.select_related("item", "user").get(id=request_id)
        user_email = getattr(req.user, "email", None)
        instrument_name = getattr(req.item, "name", "Instrument")
        return_by = getattr(req, "return_by", None)

        if not user_email:
            logger.warning(f"[Task {self.request.id}] User email missing for IssueRequest {request_id}")
            return

        subject = f"Instrument Return Reminder ({when})"
        msg = (
            "Hello,\n\n"
            f"This is a friendly reminder to return the instrument: {instrument_name}.\n"
            f"Return by: {_fmt_ist(return_by)}\n\n"
            "Thank you,\n"
            "Lab Team"
        )

        # HTML version
        html_body = _wrap_html_email(
            f"Instrument Return Reminder ({escape(when)})",
            """
            <p>Hello,</p>
            <p>This is a friendly reminder to return the instrument: <strong>{instrument}</strong>.</p>
            <p><strong>Return by:</strong> {return_by}</p>
            <p>Thank you.</p>
            """.format(instrument=escape(instrument_name), return_by=escape(_fmt_ist(return_by)))
        )

        # Email sending disabled via Celery (commented out intentionally)
        # send_mail(
        #     subject=subject,
        #     message=msg,
        #     from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        #     recipient_list=[user_email],
        #     html_message=html_body,
        #     fail_silently=False,
        # )
        # logger.info(f"[Task {self.request.id}] Sent '{when}' reminder to {user_email}")
    except IssueRequest.DoesNotExist:
        logger.warning(f"[Task {self.request.id}] IssueRequest {request_id} not found.")
    except Exception as e:
        logger.error(f"[Task {self.request.id}] Error sending reminder: {e}")


# ---------------------------
# Approval/Rejection emails
# ---------------------------
@shared_task(bind=True)
def send_issue_approved_email(self, request_id: int):
    """Send an email notifying the user that their issue request was approved."""
    try:
        req = IssueRequest.objects.select_related("item", "user").get(id=request_id)
        user_email = getattr(req.user, "email", None)
        instrument_name = getattr(req.item, "name", "Instrument")
        qty = getattr(req, "quantity", 1)
        approved_at = getattr(req, "approved_at", None)
        return_by = getattr(req, "return_by", None)

        if not user_email:
            logger.warning(f"[Task {self.request.id}] User email missing for IssueRequest {request_id}")
            return

        subject = "Instrument Request Approved"
        parts = [
            "Hello,",
            "",
            f"Good news! Your instrument request has been approved.",
            f"- Item: {instrument_name}",
            f"- Quantity: {qty}",
        ]
        if approved_at:
            parts.append(f"- Pickup time: {_fmt_ist(approved_at)}")
        if return_by:
            parts.append(f"- Return by: {_fmt_ist(return_by)}")
        remarks = getattr(req, "remarks", "")
        if remarks:
            parts.append(f"- Remarks: {remarks}")
        parts.extend(["", "Thank you,", "Lab Team"])
        msg = "\n".join(parts)

        # HTML version
        details = [
            ("Item", instrument_name),
            ("Quantity", str(qty)),
        ]
        if approved_at:
            details.append(("Pickup time", _fmt_ist(approved_at)))
        if return_by:
            details.append(("Return by", _fmt_ist(return_by)))
        remarks = getattr(req, "remarks", "")
        if remarks:
            details.append(("Remarks", remarks))

        rows = "".join(
            f"<tr><td style='padding:8px 12px; border:1px solid #e5e7eb; font-weight:600; background:#fafafa;'>{escape(k)}</td>"
            f"<td style='padding:8px 12px; border:1px solid #e5e7eb;'>{escape(v)}</td></tr>" for k,v in details
        )
        html_body = _wrap_html_email(
            "Instrument Request Approved",
            f"""
            <p>Hello,</p>
            <p>Good news! Your instrument request has been approved.</p>
            <table style="border-collapse:collapse; width:100%; margin-top:8px;">{rows}</table>
            <p style="margin-top:12px;">Thank you.</p>
            """.format(rows=rows)
        )

        # Email sending disabled via Celery (commented out intentionally)
        # send_mail(
        #     subject=subject,
        #     message=msg,
        #     from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        #     recipient_list=[user_email],
        #     html_message=html_body,
        #     fail_silently=False,
        # )
        # logger.info(f"[Task {self.request.id}] Approval email sent to {user_email} for request {request_id}")
    except IssueRequest.DoesNotExist:
        logger.warning(f"[Task {self.request.id}] IssueRequest {request_id} not found for approval email.")
    except Exception as e:
        logger.error(f"[Task {self.request.id}] Error sending approval email: {e}")


@shared_task(bind=True)
def send_issue_rejected_email(self, request_id: int):
    """Send an email notifying the user that their issue request was rejected."""
    try:
        req = IssueRequest.objects.select_related("item", "user").get(id=request_id)
        user_email = getattr(req.user, "email", None)
        instrument_name = getattr(req.item, "name", "Instrument")
        qty = getattr(req, "quantity", 1)
        reason = getattr(req, "remarks", "")

        if not user_email:
            logger.warning(f"[Task {self.request.id}] User email missing for IssueRequest {request_id}")
            return

        subject = "Instrument Request Rejected"
        parts = [
            "Hello,",
            "",
            "We’re sorry. Your instrument request could not be approved.",
            f"- Item: {instrument_name}",
            f"- Quantity: {qty}",
        ]
        if reason:
            parts.append(f"- Reason: {reason}")
        parts.extend(["", "If you have questions, please contact the lab team.", "", "Regards,", "Lab Team"])
        msg = "\n".join(parts)

        # HTML version
        details = [
            ("Item", instrument_name),
            ("Quantity", str(qty)),
        ]
        if reason:
            details.append(("Reason", reason))
        rows = "".join(
            f"<tr><td style='padding:8px 12px; border:1px solid #e5e7eb; font-weight:600; background:#fafafa;'>{escape(k)}</td>"
            f"<td style='padding:8px 12px; border:1px solid #e5e7eb;'>{escape(v)}</td></tr>" for k,v in details
        )
        html_body = _wrap_html_email(
            "Instrument Request Rejected",
            f"""
            <p>Hello,</p>
            <p>We’re sorry. Your instrument request could not be approved.</p>
            <table style="border-collapse:collapse; width:100%; margin-top:8px;">{rows}</table>
            <p style="margin-top:12px;">If you have questions, please contact the lab team.</p>
            """.format(rows=rows)
        )

        # Email sending disabled via Celery (commented out intentionally)
        # send_mail(
        #     subject=subject,
        #     message=msg,
        #     from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        #     recipient_list=[user_email],
        #     html_message=html_body,
        #     fail_silently=False,
        # )
        # logger.info(f"[Task {self.request.id}] Rejection email sent to {user_email} for request {request_id}")
    except IssueRequest.DoesNotExist:
        logger.warning(f"[Task {self.request.id}] IssueRequest {request_id} not found for rejection email.")
    except Exception as e:
        logger.error(f"[Task {self.request.id}] Error sending rejection email: {e}")


def approve_request(request_id: int, pickup_time, no_of_days: int, admin_user):
    """
    Legacy helper: approve and schedule reminders (synchronous).
    Aligns with current IssueRequest fields.
    """
    with transaction.atomic():
        req = IssueRequest.objects.select_for_update().select_related("item").get(id=request_id)
        if req.status != "pending":
            return {"message": "Request already processed."}

        # For consumables, ensure stock exists then decrement.
        item = req.item
        if item.is_consumable:
            if req.quantity > item.quantity:
                return {"message": "Not enough stock available."}
            item.quantity -= req.quantity
            item.save()

        req.status = "approved"
        req.approved_at = pickup_time or timezone.now()
        req.return_by = req.approved_at + timedelta(days=no_of_days)
        req.save()

    # Schedule reminders (disabled when REMINDER_EMAILS_ENABLED is False)
    if REMINDER_EMAILS_ENABLED:
        one_day_before = req.return_by - timedelta(days=1)
        one_hour_before = req.return_by - timedelta(hours=1)
        if one_day_before > timezone.now():
            send_reminder_email.apply_async((req.id, "1 day before"), eta=one_day_before)
        if one_hour_before > timezone.now():
            send_reminder_email.apply_async((req.id, "1 hour before"), eta=one_hour_before)

    # Send approval email immediately
    try:
        send_issue_approved_email.delay(req.id)
    except Exception:
        logger.exception("Failed to dispatch approval email task")

    return {"message": "Request approved."}


# ---------------------------
# Cache helpers
# ---------------------------
def _serialize_item_for_cache(item: Item) -> Dict[str, Any]:
    """Return a dict compatible with ItemSchema for caching/quick responses."""
    try:
        available_qty = getattr(item, "available_quantity", None)
        if available_qty is None:
            # Fallback heuristic
            available_qty = item.quantity
    except Exception:
        available_qty = item.quantity

    return {
        "id": item.id,
        "name": item.name,
        "category_id": item.category_id,
        "sub_category_id": item.sub_category_id,
        "quantity": item.quantity,
        "is_consumable": item.is_consumable,
        "location": item.location,
        "is_available": getattr(item, "is_available", True),
        "min_issue_limit": getattr(item, "min_issue_limit", 1),
        "max_issue_limit": getattr(item, "max_issue_limit", 1),
        "description": getattr(item, "description", ""),
        "available_quantity": available_qty,
    }


def _cache_item_snapshot(snapshot: Dict[str, Any]):
    item_id = snapshot.get("id")
    if not item_id:
        return
    # Set both a namespaced and a simple key for flexibility
    cache.set(f"instruments:item:{item_id}", snapshot, timeout=600)
    cache.set(f"item:{item_id}", snapshot, timeout=600)


# ---------------------------
# Cache warm-up on login
# ---------------------------
@shared_task(bind=True)
def warm_instruments_cache(self) -> None:
    """
    Pre-warm Redis with instruments data to make first UI paint instant.

    Warms:
    - All items (category=all, sub=all)
    - Items per category (sub=all)
    - All categories
    - Subcategories per category

    Key formats match intruments.api helpers so existing cache reads hit.
    """
    try:
        # Index keys (keep in sync with intruments.api)
        ITEMS_INDEX_KEY = "instruments:items:keys"
        CATEGORIES_INDEX_KEY = "instruments:categories:keys"
        SUBCATS_INDEX_KEY = "instruments:subcategories:keys"

        def add_to_index(index_key: str, key: str):
            try:
                keys = cache.get(index_key) or []
                if key not in keys:
                    keys.append(key)
                    cache.set(index_key, keys, None)
            except Exception:
                pass

        # Categories
        categories_key = "instruments:categories:all"
        categories = list(Category.objects.all().only("id", "name"))
        cache.set(categories_key, categories, timeout=600)
        add_to_index(CATEGORIES_INDEX_KEY, categories_key)

        # Subcategories per category
        for c in categories:
            subcats_key = f"instruments:subcategories:category={c.id}"
            subcats = list(SubCategory.objects.filter(category_id=c.id).only("id", "name", "category_id"))
            cache.set(subcats_key, subcats, timeout=600)
            add_to_index(SUBCATS_INDEX_KEY, subcats_key)

        # All items
        all_items_key = "instruments:items:category=all:sub=all"
        all_items = list(Item.objects.select_related("category", "sub_category").all())
        cache.set(all_items_key, all_items, timeout=300)
        add_to_index(ITEMS_INDEX_KEY, all_items_key)

        # Per-category items and per-subcategory items
        for c in categories:
            cat_items_qs = Item.objects.select_related("category", "sub_category").filter(category_id=c.id)
            # Category only
            cat_items_key = f"instruments:items:category={c.id}:sub=all"
            cat_items = list(cat_items_qs)
            cache.set(cat_items_key, cat_items, timeout=300)
            add_to_index(ITEMS_INDEX_KEY, cat_items_key)

            # Per subcategory under this category
            subcats = list(SubCategory.objects.filter(category_id=c.id).only("id", "name", "category_id"))
            for sub in subcats:
                sub_items_key = f"instruments:items:category={c.id}:sub={sub.id}"
                sub_items = list(cat_items_qs.filter(sub_category_id=sub.id))
                cache.set(sub_items_key, sub_items, timeout=300)
                add_to_index(ITEMS_INDEX_KEY, sub_items_key)

        logger.info(
            f"[Task {self.request.id}] Warmed instruments cache: cats={len(categories)}, items_all={len(all_items)}"
        )
    except Exception as e:
        logger.exception(f"warm_instruments_cache failed: {e}")


# ---------------------------
# Async write-through tasks
# ---------------------------
@shared_task
def async_update_item_fields(item_id: int, payload: Dict[str, Any]) -> bool:
    """
    Apply multiple field updates to an Item and refresh caches.
    Uses a transaction and select_for_update to avoid race conditions.
    """
    try:
        with transaction.atomic():
            item = Item.objects.select_for_update().get(id=item_id)

            # Relations
            if "category_id" in payload and payload["category_id"]:
                item.category = Category.objects.get(id=int(payload["category_id"]))
            if "sub_category_id" in payload:
                sub_id = payload.get("sub_category_id")
                item.sub_category = SubCategory.objects.get(id=int(sub_id)) if sub_id else None

            # Simple fields (only set known attributes)
            for fld in [
                "name",
                "quantity",
                "is_consumable",
                "is_available",
                "location",
                "min_issue_limit",
                "max_issue_limit",
                "description",
            ]:
                if fld in payload:
                    setattr(item, fld, payload[fld])

            item.save()

        # Refresh cache after commit
        item_refreshed = Item.objects.select_related("category", "sub_category").get(id=item_id)
        snapshot = _serialize_item_for_cache(item_refreshed)
        _cache_item_snapshot(snapshot)
        return True
    except Item.DoesNotExist:
        return False
    except Exception as e:
        logger.exception("async_update_item_fields failed: %s", e)
        return False


@shared_task
def async_approve_issue(issue_id: int, no_of_days: int = 7, send_email: bool = True, return_by_iso: Optional[str] = None) -> bool:
    """
    Approve an IssueRequest asynchronously and update related caches.
    Also schedules reminder emails.
    """
    try:
        with transaction.atomic():
            issue_request = (
                IssueRequest.objects.select_for_update()
                .select_related("item")
                .get(id=issue_id)
            )
            if issue_request.status != "pending":
                return True

            item = Item.objects.select_for_update().get(id=issue_request.item_id)
            # For consumables, quantity was already reserved on request creation (hard-lock).
            # For non-consumables, available_quantity is derived; no stock field change needed here.

            issue_request.status = "approved"
            issue_request.approved_at = timezone.now()
            if return_by_iso:
                # Prefer explicit return_by when provided (already clamped to EOD at API layer)
                try:
                    rb = datetime.fromisoformat(return_by_iso)
                    # Ensure timezone-aware in current timezone
                    if timezone.is_naive(rb):
                        rb = timezone.make_aware(rb, timezone.get_current_timezone())
                    issue_request.return_by = rb
                except Exception:
                    issue_request.return_by = issue_request.approved_at + timedelta(days=no_of_days)
            else:
                issue_request.return_by = issue_request.approved_at + timedelta(days=no_of_days)
            # Set submission_status based on item type
            try:
                if item.is_consumable:
                    # Auto-submit consumables upon approval
                    issue_request.submission_status = "submitted"
                    issue_request.submitted_at = issue_request.approved_at
                    # Ensure remarks include Consumed and submission time on next line if not already present
                    try:
                        submitted_txt = _fmt_ist(issue_request.submitted_at)
                    except Exception:
                        submitted_txt = str(issue_request.submitted_at) if issue_request.submitted_at else ""
                    line1 = "Consumed"
                    line2 = f"Submitted at {submitted_txt}" if submitted_txt else "Submitted"
                    current = (getattr(issue_request, "remarks", "") or "").strip()
                    if not current:
                        issue_request.remarks = f"{line1}\n{line2}"
                    else:
                        # Append submission time if not present
                        add_lines = []
                        if "Consumed" not in current:
                            add_lines.append(line1)
                        if "Submitted at" not in current:
                            add_lines.append(line2)
                        if add_lines:
                            issue_request.remarks = current + "\n" + "\n".join(add_lines)
                else:
                    issue_request.submission_status = "pending"
            except Exception:
                pass
            issue_request.save()

        # Update per-item cache
        snapshot = _serialize_item_for_cache(item)
        _cache_item_snapshot(snapshot)

        # Emit WS event for immediate UI update with full payload
        try:
            layer = get_channel_layer()
            async_to_sync(layer.group_send)(
                "issue_request_updates",
                {
                    "type": "send_issue_update",
                    "data": {
                        "event": "issue_request.updated",
                        "payload": {
                            "id": issue_request.id,
                            "status": issue_request.status,
                            "approved_at": issue_request.approved_at.isoformat() if issue_request.approved_at else None,
                            "return_by": issue_request.return_by.isoformat() if issue_request.return_by else None,
                            "submission_status": getattr(issue_request, "submission_status", None),
                            "submitted_at": issue_request.submitted_at.isoformat() if getattr(issue_request, "submitted_at", None) else None,
                            "remarks": getattr(issue_request, "remarks", None),
                        },
                    },
                },
            )
        except Exception:
            logger.exception("WS emit issue_request.updated failed")

        # No auto system message for consumables to avoid misclassification noise

        # Schedule reminders (disabled when REMINDER_EMAILS_ENABLED is False)
        if REMINDER_EMAILS_ENABLED:
            one_day_before = issue_request.return_by - timedelta(days=1)
            one_hour_before = issue_request.return_by - timedelta(hours=1)
            if one_day_before > timezone.now():
                send_reminder_email.apply_async((issue_request.id, "1 day before"), eta=one_day_before)
            if one_hour_before > timezone.now():
                send_reminder_email.apply_async((issue_request.id, "1 hour before"), eta=one_hour_before)

        # Schedule a system message at deadline only if not already submitted
        try:
            if issue_request.return_by and getattr(issue_request, "submission_status", None) != "submitted":
                create_deadline_system_message.apply_async((issue_request.id,), eta=issue_request.return_by)
        except Exception:
            logger.exception("Failed to schedule deadline system message")

        # Send approval email immediately
        try:
            if send_email:
                send_issue_approved_email.delay(issue_request.id)
        except Exception:
            logger.exception("Failed to dispatch approval email task")

        return True
    except IssueRequest.DoesNotExist:
        return False
    except Exception as e:
        logger.exception("async_approve_issue failed: %s", e)
        return False


@shared_task
def async_reject_issue(issue_id: int, reason: Optional[str] = "", send_email: bool = True):  # -> bool (Celery serializes return anyway)
    """
    Reject an IssueRequest asynchronously. Does not alter item stock.
    """
    try:
        with transaction.atomic():
            issue_request = (
                IssueRequest.objects.select_for_update()
                .select_related("item")
                .get(id=issue_id)
            )
            if issue_request.status != "pending":
                return True
            issue_request.status = "rejected"
            if reason:
                issue_request.remarks = reason
            issue_request.save()

        # No stock change, but we can refresh cache for consistency
        item = issue_request.item
        snapshot = _serialize_item_for_cache(item)
        _cache_item_snapshot(snapshot)

        # Emit WS event for immediate UI update
        try:
            layer = get_channel_layer()
            async_to_sync(layer.group_send)(
                "issue_request_updates",
                {
                    "type": "send_issue_update",
                    "data": {
                        "event": "issue_request.updated",
                        "payload": {
                            "id": issue_request.id,
                            "status": issue_request.status,
                            "approved_at": issue_request.approved_at.isoformat() if issue_request.approved_at else None,
                            "return_by": issue_request.return_by.isoformat() if issue_request.return_by else None,
                            "submission_status": getattr(issue_request, "submission_status", None),
                            "submitted_at": issue_request.submitted_at.isoformat() if getattr(issue_request, "submitted_at", None) else None,
                        },
                    },
                },
            )
        except Exception:
            logger.exception("WS emit issue_request.updated (reject) failed")

        # Send rejection email
        try:
            if send_email:
                send_issue_rejected_email.delay(issue_id)
        except Exception:
            logger.exception("Failed to dispatch rejection email task")
        return True
    except IssueRequest.DoesNotExist:
        return False
    except Exception as e:
        logger.exception("async_reject_issue failed: %s", e)
        return False


# ---------------------------
# Bulk consolidated emails
# ---------------------------
@shared_task(bind=True)
def send_bulk_issue_approved_email(self, request_ids: list[int], return_days: Optional[int] = None, return_by_iso: Optional[str] = None):
    """Send one consolidated approval email per user for the provided request IDs."""
    try:
        qs = IssueRequest.objects.filter(id__in=request_ids).select_related("item", "user")
        by_user: Dict[int, list[IssueRequest]] = defaultdict(list)
        for r in qs:
            by_user[r.user_id].append(r)

        # Determine a common return_by if provided
        common_return_by = None
        if return_by_iso:
            try:
                # Django may parse isoformat automatically when used elsewhere; here we just display what we got.
                # We don't rely on parsing to avoid tz pitfalls.
                pass
            except Exception:
                pass

        for user_id, items in by_user.items():
            user = items[0].user
            email = getattr(user, "email", None)
            if not email:
                continue

            subject = f"Instrument Requests Approved ({len(items)} items)"
            lines = [
                "Hello,",
                "",
                "Your instrument requests have been approved.",
                "",
                "Items:",
            ]
            for r in items:
                rb = getattr(r, "return_by", None)
                rb_txt = _fmt_ist(rb) if rb else (return_by_iso or (f"+{return_days} days" if return_days else ""))
                parts = [
                    f"- {r.item.name} (qty: {r.quantity})",
                    f"  Pickup: {_fmt_ist(getattr(r, 'approved_at', None))}" if getattr(r, 'approved_at', None) else None,
                    f"  Return by: {rb_txt}" if rb_txt else None,
                    f"  Remarks: {getattr(r, 'remarks', '')}" if getattr(r, 'remarks', '') else None,
                ]
                lines.extend([p for p in parts if p])

            lines.extend(["", "Thank you,", "Lab Team"])
            msg = "\n".join(lines)

            # HTML table of approved items
            table_rows = []
            for r in items:
                pickup_txt = _fmt_ist(getattr(r, 'approved_at', None)) if getattr(r, 'approved_at', None) else "-"
                rb = getattr(r, 'return_by', None)
                rb_txt = _fmt_ist(rb) if rb else (return_by_iso or (f"+{return_days} days" if return_days else "-"))
                remarks_txt = getattr(r, 'remarks', '') or "-"
                table_rows.append(
                    """
                    <tr>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb;'>{item}</td>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb; text-align:center;'>{qty}</td>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb;'>{pickup}</td>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb;'>{retby}</td>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb;'>{remarks}</td>
                    </tr>
                    """.format(
                        item=escape(r.item.name), qty=escape(str(r.quantity)), pickup=escape(pickup_txt), retby=escape(str(rb_txt)), remarks=escape(remarks_txt)
                    )
                )
            html_body = _wrap_html_email(
                f"Instrument Requests Approved ({len(items)} items)",
                """
                <p>Hello,</p>
                <p>Your instrument requests have been approved.</p>
                <table style="border-collapse:collapse; width:100%; margin-top:8px;">
                  <thead>
                    <tr>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:left;'>Item</th>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:center;'>Qty</th>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:left;'>Pickup</th>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:left;'>Return By</th>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:left;'>Remarks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows}
                  </tbody>
                </table>
                <p style="margin-top:12px;">Thank you.</p>
                """.format(rows="".join(table_rows))
            )

            # Email sending disabled via Celery (commented out intentionally)
            # send_mail(
            #     subject=subject,
            #     message=msg,
            #     from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
            #     recipient_list=[email],
            #     html_message=html_body,
            #     fail_silently=False,
            # )
            # logger.info(f"[Task {self.request.id}] Sent consolidated approved email to {email} ({len(items)} items)")
    except Exception as e:
        logger.exception("send_bulk_issue_approved_email failed: %s", e)


@shared_task(bind=True)
def send_bulk_issue_rejected_email(self, request_ids: list[int]):
    """Send one consolidated rejection email per user for the provided request IDs."""
    try:
        qs = IssueRequest.objects.filter(id__in=request_ids).select_related("item", "user")
        by_user: Dict[int, list[IssueRequest]] = defaultdict(list)
        for r in qs:
            by_user[r.user_id].append(r)

        for user_id, items in by_user.items():
            user = items[0].user
            email = getattr(user, "email", None)
            if not email:
                continue

            subject = f"Instrument Requests Rejected ({len(items)} items)"
            lines = [
                "Hello,",
                "",
                "We’re sorry. The following instrument requests were rejected:",
                "",
                "Items:",
            ]
            for r in items:
                reason = getattr(r, "remarks", "")
                parts = [
                    f"- {r.item.name} (qty: {r.quantity})",
                    f"  Reason: {reason}" if reason else None,
                ]
                lines.extend([p for p in parts if p])

            lines.extend(["", "If you have questions, please contact the lab team.", "", "Regards,", "Lab Team"])
            msg = "\n".join(lines)

            # HTML table of rejected items
            table_rows = []
            for r in items:
                reason = getattr(r, 'remarks', '') or "-"
                table_rows.append(
                    """
                    <tr>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb;'>{item}</td>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb; text-align:center;'>{qty}</td>
                      <td style='padding:8px 12px; border:1px solid #e5e7eb;'>{reason}</td>
                    </tr>
                    """.format(
                        item=escape(r.item.name), qty=escape(str(r.quantity)), reason=escape(reason)
                    )
                )

            html_body = _wrap_html_email(
                f"Instrument Requests Rejected ({len(items)} items)",
                """
                <p>Hello,</p>
                <p>We’re sorry. The following instrument requests were rejected:</p>
                <table style="border-collapse:collapse; width:100%; margin-top:8px;">
                  <thead>
                    <tr>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:left;'>Item</th>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:center;'>Qty</th>
                      <th style='padding:8px 12px; border:1px solid #e5e7eb; background:#fafafa; text-align:left;'>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows}
                  </tbody>
                </table>
                <p style=\"margin-top:12px;\">If you have questions, please contact the lab team.</p>
                """.format(rows="".join(table_rows))
            )

            # Email sending disabled via Celery (commented out intentionally)
            # send_mail(
            #     subject=subject,
            #     message=msg,
            #     from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
            #     recipient_list=[email],
            #     html_message=html_body,
            #     fail_silently=False,
            # )
            # logger.info(f"[Task {self.request.id}] Sent consolidated rejected email to {email} ({len(items)} items)")
    except Exception as e:
        logger.exception("send_bulk_issue_rejected_email failed: %s", e)


# ---------------------------
# Message email + system message generation
# ---------------------------
@shared_task(bind=True)
def send_issue_message_email(self, request_id: int, message_id: int):
    """Send an email notification for an issue message to the request owner."""
    try:
        req = IssueRequest.objects.select_related("user", "item").get(id=request_id)
        msg = IssueMessage.objects.get(id=message_id, issue_request_id=request_id)
        user_email = getattr(req.user, "email", None)
        if not user_email:
            return

        subject = f"Update on your instrument request (#{request_id})"
        body = (
            f"Hello,\n\n"
            f"There is a new message on your instrument request for '{getattr(req.item, 'name', 'Instrument')}'.\n\n"
            f"Message: {msg.text}\n\n"
            f"Regards,\nLab Team"
        )
        # HTML body
        html_body = _wrap_html_email(
            "New Message on Your Request",
            """
            <p>Hello,</p>
            <p>There is a new message on your instrument request for <strong>{item}</strong>.</p>
            <blockquote style="margin:12px 0; padding:12px; background:#f9fafb; border-left:4px solid #6366f1;">{text}</blockquote>
            <p>Regards,<br/>Lab Team</p>
            """.format(item=escape(getattr(req.item, 'name', 'Instrument')), text=escape(msg.text or ""))
        )
        # Send message notification email (guarded by DEBUG flag)
        if getattr(settings, "DEBUG", True):
            send_mail(
                subject,
                body,
                getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
                [user_email],
                html_message=html_body,
                fail_silently=False,
            )
    except Exception:
        # Avoid deep traceback recursion on some Python versions
        logger.error("send_issue_message_email failed", exc_info=False)


@shared_task(bind=True)
def create_deadline_system_message(self, request_id: int):
    """Create a system message at the return deadline and optionally email the user."""
    try:
        req = IssueRequest.objects.select_related("user", "item").only("id", "return_by", "submission_status").get(id=request_id)
        # Safety checks to avoid premature or duplicate messages due to timezone/clock skew
        now = timezone.now()
        rb = getattr(req, "return_by", None)
        sub_status = getattr(req, "submission_status", None)
        # If already submitted, skip
        if sub_status == "submitted":
            return
        # If return_by exists and is still in the future (allow 30s skew), reschedule instead of sending now
        if rb and (now + timedelta(seconds=30)) < rb:
            try:
                create_deadline_system_message.apply_async((req.id,), eta=rb)
            except Exception:
                # Avoid recursive exception formatting
                logger.error("Reschedule deadline system message failed", exc_info=False)
            return

        text = "Return deadline reached. Please return the instrument as soon as possible."
        msg = IssueMessage.objects.create(issue_request=req, msg_type="system", text=text)
        # WS emission will be handled on API side typically, but we can also notiy here via channel layer if needed.
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            layer = get_channel_layer()
            async_to_sync(layer.group_send)(
                "issue_request_updates",
                {"type": "send_issue_update", "data": {"event": "issue_request.message", "payload": {
                    "id": msg.id,
                    "issue_request_id": req.id,
                    "msg_type": msg.msg_type,
                    "text": msg.text,
                    "created_at": msg.created_at.isoformat(),
                    "creator_id": msg.creator_id,
                }}}
            )
        except Exception:
            pass
        # Update Redis cache for messages
        try:
            ck = f"instruments:issue_messages:{req.id}"
            entry = {
                "id": msg.id,
                "issue_request_id": req.id,
                "msg_type": msg.msg_type,
                "text": msg.text,
                "created_at": msg.created_at,
                "creator_id": msg.creator_id,
                "sender_name": "System",
            }
            lst = cache.get(ck)
            if isinstance(lst, list):
                lst = [entry] + lst
                if len(lst) > MESSAGES_CACHE_MAX:
                    lst = lst[:MESSAGES_CACHE_MAX]
                cache.set(ck, lst, timeout=MESSAGES_CACHE_TIMEOUT)
            else:
                cache.set(ck, [entry], timeout=MESSAGES_CACHE_TIMEOUT)
        except Exception:
            pass
        # Optionally email
        try:
            send_issue_message_email.delay(req.id, msg.id)
        except Exception:
            pass
    except Exception:
        # Avoid recursive exception formatting on traceback
        logger.error("create_deadline_system_message failed", exc_info=False)