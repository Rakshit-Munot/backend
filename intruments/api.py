from typing import Optional

from ninja import NinjaAPI, Schema
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import Item, Category, SubCategory, IssueRequest, IssueMessage
from zoneinfo import ZoneInfo
from .tasks import (
    async_update_item_fields,
    async_approve_issue,
    async_reject_issue,
    send_issue_rejected_email,
    send_bulk_issue_approved_email,
    send_bulk_issue_rejected_email,
)
from .schemas import (
    ItemSchema, ItemIn,
    CategorySchema, SubCategorySchema,
    CategoryIn, SubCategoryIn,
    IssueRequestIn, IssueRequestSchema,
    IssueRequestListSchema, ItemSummary, UserSummary,
    ApproveRequestIn, RejectRequestIn, BulkApproveIn, BulkRejectIn,
    IssueMessageIn, IssueMessageSchema, SubmitReturnIn,
)

api = NinjaAPI(urls_namespace="instruments")
User = get_user_model()

# ---------------------------
# Cache index keys
# ---------------------------
ITEMS_INDEX_KEY = "instruments:items:keys"
CATEGORIES_INDEX_KEY = "instruments:categories:keys"
SUBCATS_INDEX_KEY = "instruments:subcategories:keys"
MESSAGES_CACHE_TIMEOUT = 300  # seconds
MESSAGES_CACHE_MAX = 200

# ---------------------------
# Cache helpers
# ---------------------------
def _cache_get(key):
    return cache.get(key)

def _cache_set_indexed(key, value, timeout: Optional[int], index_key: str):
    cache.set(key, value, timeout)
    try:
        keys = cache.get(index_key) or []
        if key not in keys:
            keys.append(key)
            cache.set(index_key, keys, None)
    except Exception:
        pass

def _cache_invalidate_index(index_key: str):
    keys = cache.get(index_key) or []
    for k in keys:
        try:
            cache.delete(k)
        except Exception:
            pass
    try:
        cache.delete(index_key)
    except Exception:
        pass

def _items_cache_key(category: Optional[int], subcategory: Optional[int]) -> str:
    return f"instruments:items:category={category if category is not None else 'all'}:sub={subcategory if subcategory is not None else 'all'}"

def _categories_cache_key() -> str:
    return "instruments:categories:all"

def _subcats_cache_key(category_id: int) -> str:
    return f"instruments:subcategories:category={category_id}"

def _messages_cache_key(request_id: int) -> str:
    return f"instruments:issue_messages:{request_id}"

# ---------------------------
# Shaping helpers
# ---------------------------
def _is_admin(request) -> bool:
    try:
        user = getattr(request, "user", None)
        return bool(user and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))
    except Exception:
        return False

def _shape_item_for_user(request, item_or_snapshot) -> dict:
    """Return a plain dict for ItemSchema, masking sensitive fields for non-admins."""
    try:
        if isinstance(item_or_snapshot, dict):
            src = item_or_snapshot
            data = {
                "id": src.get("id"),
                "name": src.get("name", ""),
                "category_id": src.get("category_id"),
                "sub_category_id": src.get("sub_category_id"),
                "quantity": src.get("quantity", 0),
                "is_consumable": bool(src.get("is_consumable", False)),
                "location": src.get("location", ""),
                "is_available": bool(src.get("is_available", True)),
                "min_issue_limit": src.get("min_issue_limit", 1),
                "max_issue_limit": src.get("max_issue_limit", src.get("min_issue_limit", 1)),
                "description": src.get("description", ""),
                "available_quantity": src.get("available_quantity", src.get("quantity", 0)),
            }
        else:
            it = item_or_snapshot
            data = {
                "id": it.id,
                "name": it.name,
                "category_id": it.category_id,
                "sub_category_id": it.sub_category_id,
                "quantity": it.quantity,
                "is_consumable": bool(getattr(it, "is_consumable", False)),
                "location": getattr(it, "location", "") or "",
                "is_available": bool(getattr(it, "is_available", True)),
                "min_issue_limit": getattr(it, "min_issue_limit", 1),
                "max_issue_limit": getattr(it, "max_issue_limit", getattr(it, "min_issue_limit", 1)),
                "description": getattr(it, "description", "") or "",
                "available_quantity": getattr(it, "available_quantity", getattr(it, "quantity", 0)),
            }
        # Mask location for non-admin users
        if not _is_admin(request):
            data["location"] = ""
        return data
    except Exception:
        # Fallback minimal shape
        try:
            it = item_or_snapshot
            base = {"id": getattr(it, "id", None), "name": getattr(it, "name", ""), "category_id": getattr(it, "category_id", None), "sub_category_id": getattr(it, "sub_category_id", None), "quantity": getattr(it, "quantity", 0), "is_consumable": bool(getattr(it, "is_consumable", False)), "is_available": bool(getattr(it, "is_available", True)), "min_issue_limit": getattr(it, "min_issue_limit", 1), "max_issue_limit": getattr(it, "max_issue_limit", getattr(it, "min_issue_limit", 1)), "description": getattr(it, "description", "")}
        except Exception:
            base = {"id": None, "name": "", "category_id": None, "sub_category_id": None, "quantity": 0, "is_consumable": False, "is_available": True, "min_issue_limit": 1, "max_issue_limit": 1, "description": ""}
        base["available_quantity"] = base.get("quantity", 0)
        if not _is_admin(request):
            base["location"] = ""
        else:
            base["location"] = getattr(item_or_snapshot, "location", "") if not isinstance(item_or_snapshot, dict) else item_or_snapshot.get("location", "")
        return base

# ---------------------------
# WebSocket helpers
# ---------------------------
def _ws_emit_instrument(event: str, payload: dict):
    try:
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(
            "instrument_updates",
            {"type": "send_instrument_update", "data": {"event": event, "payload": payload}},
        )
    except Exception:
        pass


def _ws_emit_issue(event: str, payload: dict):
    try:
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(
            "issue_request_updates",
            {"type": "send_issue_update", "data": {"event": event, "payload": payload}},
        )
    except Exception:
        pass
    
def _ws_emit_message(event: str, payload: dict):
    try:
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(
            "issue_request_updates",
            {"type": "send_issue_update", "data": {"event": event, "payload": payload}},
        )
    except Exception:
        pass

# ---------------------------
# User display helper
# ---------------------------
def _user_display(user) -> str:
    try:
        if not user:
            return ""
        name = ""
        try:
            # Prefer full name if available
            full = getattr(user, "get_full_name", None)
            if callable(full):
                name = (full() or "").strip()
        except Exception:
            name = ""
        if not name:
            name = (getattr(user, "username", None) or getattr(user, "email", None) or "").strip()
        return name or "Admin"
    except Exception:
        return "Admin"

# ---------------------------
# Date helpers
# ---------------------------
def _clamp_to_eod(dt):
    """Clamp a date/datetime to end-of-day (23:59:59) in current timezone, ensuring tz-aware."""
    try:
        tz = timezone.get_current_timezone()
        # Ensure datetime
        # If a date-only slips through, it will still be a datetime with midnight; we'll normalize below.
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, tz)
        local = timezone.localtime(dt, tz)
        return local.replace(hour=23, minute=59, second=59, microsecond=0)
    except Exception:
        return dt

# ---------------------------
# Signals: invalidate caches
# ---------------------------
@receiver([post_save, post_delete], sender=Item)
def _on_item_changed(sender, **kwargs):
    _cache_invalidate_index(ITEMS_INDEX_KEY)

@receiver([post_save, post_delete], sender=Category)
def _on_category_changed(sender, **kwargs):
    _cache_invalidate_index(CATEGORIES_INDEX_KEY)
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)

@receiver([post_save, post_delete], sender=SubCategory)
def _on_subcategory_changed(sender, **kwargs):
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)

# ---------------------------
# Local request schemas
# ---------------------------
class CategoryUpdate(Schema):
    name: str

class SubCategoryUpdate(Schema):
    name: str

# ---------------------------
# ITEM ROUTES
# ---------------------------
@api.get("/items/{item_id}", response=ItemSchema)
def get_item(request, item_id: int):
    # Try cache first
    cache_key = f"instruments:item:{item_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return _shape_item_for_user(request, cached)

    try:
        item = Item.objects.select_related("category", "sub_category").get(id=item_id)
        # Cache snapshot
        snapshot = {
            "id": item.id,
            "name": item.name,
            "category_id": item.category_id,
            "sub_category_id": item.sub_category_id,
            "quantity": item.quantity,
            "is_consumable": item.is_consumable,
            "location": item.location,
            "is_available": item.is_available,
            "min_issue_limit": item.min_issue_limit,
            "max_issue_limit": item.max_issue_limit,
            "description": item.description,
            "available_quantity": item.available_quantity,
        }
        cache.set(cache_key, snapshot, timeout=600)
        return _shape_item_for_user(request, snapshot)
    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)

@api.put("/items/{item_id}", response=ItemSchema)
def update_item(request, item_id: int, data: ItemIn):
    """
    Update fields: name, quantity, is_consumable, location, min_issue_limit, max_issue_limit, description, remarks,
    category and optional subcategory.
    """
    try:
        item = Item.objects.get(id=item_id)
    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)

    category_obj = get_object_or_404(Category, id=data.category_id)
    subcategory_obj = get_object_or_404(SubCategory, id=data.sub_category_id) if data.sub_category_id else None

    # Validate limits
    min_limit = max(1, int(data.min_issue_limit))
    max_limit = max(min_limit, int(data.max_issue_limit))

    # Build immediate cache snapshot and enqueue async update (write-through)
    payload = {
        "category_id": category_obj.id,
        "sub_category_id": subcategory_obj.id if subcategory_obj else None,
        "name": data.name,
        "quantity": int(data.quantity),
        "is_consumable": bool(data.is_consumable),
        "location": data.location or "",
        "is_available": bool(data.is_available),
        "min_issue_limit": min_limit,
        "max_issue_limit": max_limit,
        "description": data.description or "",
    }

    # Cache now for instant UI
    snapshot = {
        "id": item.id,
        **payload,
        "available_quantity": item.available_quantity,
    }
    cache.set(f"instruments:item:{item.id}", snapshot, timeout=600)

    # Queue DB update
    async_update_item_fields.delay(item.id, payload)

    # Invalidate list caches so next list fetch refetches; detail is already cached
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    # Notify listeners
    _ws_emit_instrument("item.updated", snapshot)
    return _shape_item_for_user(request, snapshot)

@api.delete("/items/{item_id}")
def delete_item(request, item_id: int):
    try:
        item = Item.objects.get(id=item_id)
        item.delete()
        _cache_invalidate_index(ITEMS_INDEX_KEY)
        # Notify listeners
        _ws_emit_instrument("item.deleted", {"id": item_id, "sub_category_id": item.sub_category_id})
        return {"success": True}
    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)

class ItemIssueRequest(Schema):
    quantity: int

@api.post("/items/{item_id}/issue", response=ItemSchema)
def issue_item(request, item_id: int, data: ItemIssueRequest):
    """
    Direct issue (consumables only). Decrements stock after validating limits and availability.
    """
    try:
        item = Item.objects.get(id=item_id)
    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)

    # ensure item is available for issue
    if not item.is_available:
        return api.create_response(request, {"detail": "Item is currently not available for issue."}, status=400)

    if not item.is_consumable:
        return api.create_response(request, {"detail": "Direct issue allowed only for consumables."}, status=400)

    requested = int(data.quantity)
    if requested <= 0:
        return api.create_response(request, {"detail": "Quantity must be greater than 0."}, status=400)
    if requested < item.min_issue_limit:
        return api.create_response(request, {"detail": f"Minimum issue quantity is {item.min_issue_limit}."}, status=400)
    if requested > item.max_issue_limit:
        return api.create_response(request, {"detail": f"Maximum issue quantity is {item.max_issue_limit}."}, status=400)
    if requested > item.quantity:
        return api.create_response(request, {"detail": "You can't issue more than available quantity."}, status=400)

    # Update cache immediately and enqueue background DB write
    new_qty = item.quantity - requested
    cache.set(
        f"instruments:item:{item.id}",
        {
            "id": item.id,
            "name": item.name,
            "category_id": item.category_id,
            "sub_category_id": item.sub_category_id,
            "quantity": new_qty,
            "is_consumable": item.is_consumable,
            "location": item.location,
            "is_available": item.is_available,
            "min_issue_limit": item.min_issue_limit,
            "max_issue_limit": item.max_issue_limit,
            "description": item.description,
            "available_quantity": new_qty,  # consumable simple model
        },
        timeout=600,
    )
    async_update_item_fields.delay(item.id, {"quantity": new_qty})
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    # Return updated item (sync object); optional: return cached snapshot
    item.quantity = new_qty
    _ws_emit_instrument("item.updated", {
        "id": item.id,
        "name": item.name,
        "category_id": item.category_id,
        "sub_category_id": item.sub_category_id,
        "quantity": item.quantity,
        "is_consumable": item.is_consumable,
        "location": item.location,
        "is_available": item.is_available,
        "min_issue_limit": item.min_issue_limit,
        "max_issue_limit": item.max_issue_limit,
        "description": item.description,
        "available_quantity": item.quantity,
    })
    return _shape_item_for_user(request, item)

@api.get("/items", response=list[ItemSchema])
def list_items(request, category: Optional[int] = None, subcategory: Optional[int] = None):
    cache_key = _items_cache_key(category, subcategory)
    items = _cache_get(cache_key)
    if items is not None:
        try:
            return [_shape_item_for_user(request, it) for it in items]
        except Exception:
            return items

    qs = Item.objects.select_related("category", "sub_category").all()
    if category is not None:
        qs = qs.filter(category_id=category)
    if subcategory is not None:
        qs = qs.filter(sub_category_id=subcategory)

    items = list(qs)
    _cache_set_indexed(cache_key, items, timeout=300, index_key=ITEMS_INDEX_KEY)
    return [_shape_item_for_user(request, it) for it in items]

@api.post("/items", response=ItemSchema)
def create_item(request, item: ItemIn):
    """
    Create new item with fields: name, quantity, is_consumable, location, min_issue_limit, max_issue_limit,
    optional description/remarks, category and optional subcategory.
    """
    category_obj = get_object_or_404(Category, id=item.category_id)
    subcategory_obj = get_object_or_404(SubCategory, id=item.sub_category_id) if item.sub_category_id else None

    min_limit = max(1, int(item.min_issue_limit))
    max_limit = max(min_limit, int(item.max_issue_limit))

    new_item = Item.objects.create(
        category=category_obj,
        sub_category=subcategory_obj,
        name=item.name,
        quantity=int(item.quantity),
        is_consumable=bool(item.is_consumable),
        is_available=bool(item.is_available),
        location=item.location or "",
        min_issue_limit=min_limit,
        max_issue_limit=max_limit,
        description=item.description or "",
    )

    # Cache the new item for instant reads
    cache.set(
        f"instruments:item:{new_item.id}",
        {
            "id": new_item.id,
            "name": new_item.name,
            "category_id": new_item.category_id,
            "sub_category_id": new_item.sub_category_id,
            "quantity": new_item.quantity,
            "is_consumable": new_item.is_consumable,
            "location": new_item.location,
            "is_available": new_item.is_available,
            "min_issue_limit": new_item.min_issue_limit,
            "max_issue_limit": new_item.max_issue_limit,
            "description": new_item.description,
            "available_quantity": new_item.available_quantity,
        },
        timeout=600,
    )
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    # Notify listeners (shape like snapshot)
    _ws_emit_instrument("item.created", {
        "id": new_item.id,
        "name": new_item.name,
        "category_id": new_item.category_id,
        "sub_category_id": new_item.sub_category_id,
        "quantity": new_item.quantity,
        "is_consumable": new_item.is_consumable,
        "location": new_item.location,
        "is_available": new_item.is_available,
        "min_issue_limit": new_item.min_issue_limit,
        "max_issue_limit": new_item.max_issue_limit,
        "description": new_item.description,
        "available_quantity": new_item.available_quantity,
    })
    return _shape_item_for_user(request, new_item)

# ---------------------------
# CATEGORY ROUTES
# ---------------------------
@api.get("/categories", response=list[CategorySchema])
def list_categories(request):
    cache_key = _categories_cache_key()
    cats = _cache_get(cache_key)
    if cats is not None:
        return cats

    qs = Category.objects.all().only("id", "name")
    cats = list(qs)
    _cache_set_indexed(cache_key, cats, timeout=600, index_key=CATEGORIES_INDEX_KEY)
    return cats

@api.post("/categories", response=CategorySchema)
def create_category(request, data: CategoryIn):
    if Category.objects.filter(name__iexact=data.name).exists():
        return api.create_response(request, {"detail": "Category already exists."}, status=400)
    cat = Category.objects.create(name=data.name)
    _cache_invalidate_index(CATEGORIES_INDEX_KEY)
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    return cat

@api.put("/categories/{category_id}", response=CategorySchema)
def update_category(request, category_id: int, data: CategoryUpdate):
    """
    Update category name.
    """
    new_name = (data.name or "").strip()
    if not new_name:
        return api.create_response(request, {"detail": "Name is required."}, status=400)

    cat = get_object_or_404(Category, id=category_id)
    if Category.objects.filter(name__iexact=new_name).exclude(id=category_id).exists():
        return api.create_response(request, {"detail": "Category with this name already exists."}, status=400)

    cat.name = new_name
    cat.save()

    # Explicit cache invalidation (signals also handle this)
    _cache_invalidate_index(CATEGORIES_INDEX_KEY)
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    # Notify listeners
    try:
        _ws_emit_instrument("category.updated", {"id": cat.id, "name": cat.name})
    except Exception:
        pass
    return cat

@api.delete("/categories/{category_id}")
def delete_category(request, category_id: int):
    """
    Delete a category if it has no subcategories or items.
    """
    cat = get_object_or_404(Category, id=category_id)

    if SubCategory.objects.filter(category_id=category_id).exists():
        return api.create_response(
            request, {"detail": "Cannot delete category with existing subcategories."}, status=400
        )
    if Item.objects.filter(category_id=category_id).exists():
        return api.create_response(
            request, {"detail": "Cannot delete category with existing items."}, status=400
        )

    cat.delete()
    _cache_invalidate_index(CATEGORIES_INDEX_KEY)
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    try:
        _ws_emit_instrument("category.deleted", {"id": category_id})
    except Exception:
        pass
    return {"success": True}

# ---------------------------
# SUBCATEGORY ROUTES
# ---------------------------
@api.get("/subcategories", response=list[SubCategorySchema])
def list_subcategories(request, category_id: int):
    cache_key = _subcats_cache_key(category_id)
    subcats = _cache_get(cache_key)
    if subcats is not None:
        return subcats

    qs = SubCategory.objects.filter(category_id=category_id).only("id", "name", "category_id")
    subcats = list(qs)
    _cache_set_indexed(cache_key, subcats, timeout=600, index_key=SUBCATS_INDEX_KEY)
    return subcats

@api.post("/subcategories", response=SubCategorySchema)
def create_subcategory(request, data: SubCategoryIn):
    if SubCategory.objects.filter(name__iexact=data.name, category_id=data.category_id).exists():
        return api.create_response(request, {"detail": "Subcategory already exists for this category."}, status=400)
    sub = SubCategory.objects.create(name=data.name, category_id=data.category_id)
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    return sub

@api.put("/subcategories/{subcategory_id}", response=SubCategorySchema)
def update_subcategory(request, subcategory_id: int, data: SubCategoryUpdate):
    """
    Update subcategory name (within the same category).
    """
    new_name = (data.name or "").strip()
    if not new_name:
        return api.create_response(request, {"detail": "Name is required."}, status=400)

    sub = get_object_or_404(SubCategory, id=subcategory_id)
    if SubCategory.objects.filter(name__iexact=new_name, category_id=sub.category_id).exclude(id=subcategory_id).exists():
        return api.create_response(request, {"detail": "Subcategory with this name already exists in this category."}, status=400)

    sub.name = new_name
    sub.save()

    # Explicit cache invalidation (signals also handle this)
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    try:
        _ws_emit_instrument("subcategory.updated", {"id": sub.id, "name": sub.name, "category_id": sub.category_id})
    except Exception:
        pass
    return sub

@api.delete("/subcategories/{subcategory_id}")
def delete_subcategory(request, subcategory_id: int):
    """
    Delete a subcategory if it has no items.
    """
    sub = get_object_or_404(SubCategory, id=subcategory_id)

    if Item.objects.filter(sub_category_id=subcategory_id).exists():
        return api.create_response(
            request, {"detail": "Cannot delete subcategory with existing items."}, status=400
        )

    sub.delete()
    _cache_invalidate_index(SUBCATS_INDEX_KEY)
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    try:
        _ws_emit_instrument("subcategory.deleted", {"id": subcategory_id, "category_id": sub.category_id})
    except Exception:
        pass
    return {"success": True}

# ---------------------------
# ISSUE REQUEST ROUTES
# ---------------------------
@api.post("/issue-requests/", response=IssueRequestSchema)
def create_issue_request(request, data: IssueRequestIn):
    user = request.user
    item = get_object_or_404(Item, id=data.item_id)

    if data.quantity <= 0:
        return api.create_response(request, {"detail": "Quantity must be greater than 0."}, status=400)
    if data.quantity < item.min_issue_limit:
        return api.create_response(request, {"detail": f"Minimum issue quantity is {item.min_issue_limit}."}, status=400)
    if data.quantity > item.max_issue_limit:
        return api.create_response(request, {"detail": f"Maximum issue quantity is {item.max_issue_limit}."}, status=400)
    if data.quantity > item.available_quantity:
        return api.create_response(request, {"detail": "Requested quantity exceeds available stock."}, status=400)

    # Hard-lock: for consumables, decrement immediately to reserve
    if item.is_consumable:
        with transaction.atomic():
            item = Item.objects.select_for_update().get(id=item.id)
            if data.quantity > item.quantity:
                return api.create_response(request, {"detail": "Requested quantity exceeds available stock."}, status=400)
            item.quantity -= int(data.quantity)
            item.save(update_fields=["quantity"])
            issue_request = IssueRequest.objects.create(
                item=item,
                user=user,
                quantity=int(data.quantity),
                remarks=(data.remarks or ""),
                status='pending',
            )
    else:
        # Non-consumable: create pending which will be counted against availability
        issue_request = IssueRequest.objects.create(
            item=item,
            user=user,
            quantity=int(data.quantity),
            remarks=(data.remarks or ""),
            status='pending',
        )
    # Update cache snapshot for item
    try:
        cache.set(
            f"instruments:item:{item.id}",
            {
                "id": item.id,
                "name": item.name,
                "category_id": item.category_id,
                "sub_category_id": item.sub_category_id,
                "quantity": item.quantity,
                "is_consumable": item.is_consumable,
                "location": item.location,
                "is_available": item.is_available,
                "min_issue_limit": item.min_issue_limit,
                "max_issue_limit": item.max_issue_limit,
                "description": item.description,
                "available_quantity": getattr(item, "available_quantity", item.quantity),
            },
            timeout=600,
        )
        _cache_invalidate_index(ITEMS_INDEX_KEY)
        _ws_emit_instrument("item.updated", {
            "id": item.id,
            "name": item.name,
            "category_id": item.category_id,
            "sub_category_id": item.sub_category_id,
            "quantity": item.quantity,
            "is_consumable": item.is_consumable,
            "location": item.location,
            "is_available": item.is_available,
            "min_issue_limit": item.min_issue_limit,
            "max_issue_limit": item.max_issue_limit,
            "description": item.description,
            "available_quantity": getattr(item, "available_quantity", item.quantity),
        })
    except Exception:
        pass
    try:
        _ws_emit_issue("issue_request.created", {
            "id": issue_request.id,
            "item": {"id": item.id, "name": item.name},
            "user": {"id": user.id, "name": getattr(user, "username", None), "email": getattr(user, "email", None)},
            "quantity": issue_request.quantity,
            "status": issue_request.status,
            "created_at": issue_request.created_at.isoformat() if hasattr(issue_request, "created_at") else None,
            "submission_status": issue_request.submission_status,
            "submitted_at": issue_request.submitted_at.isoformat() if getattr(issue_request, "submitted_at", None) else None,
        })
    except Exception:
        pass
    return issue_request

@api.get("/issue-requests/", response=list[IssueRequestListSchema])
def list_issue_requests(
    request,
    status: Optional[str] = None,
    scope: Optional[str] = None,
    submission_status: Optional[str] = None,
    sort_by: Optional[str] = None,
    order: Optional[str] = None,
    user_id: Optional[int] = None,
    user_email: Optional[str] = None,
    roll_number: Optional[str] = None,
    sort: Optional[str] = None,
):
    qs = IssueRequest.objects.select_related("item", "user").all()
    if status:
        qs = qs.filter(status=status)
    if submission_status:
        qs = qs.filter(submission_status=submission_status)
    # Filter by specific student (any combination)
    try:
        if user_id is not None:
            qs = qs.filter(user_id=int(user_id))
    except Exception:
        pass
    try:
        if user_email:
            qs = qs.filter(user__email__iexact=user_email.strip())
    except Exception:
        pass
    try:
        if roll_number:
            qs = qs.filter(user__student_profile__roll_number__iexact=roll_number.strip())
    except Exception:
        pass
    # Scoping: scope=all to fetch all, scope=mine to fetch only current user's requests.
    # Default behavior: non-staff users are scoped to their own requests.
    try:
        if scope == "mine":
            qs = qs.filter(user_id=request.user.id)
        elif scope == "all":
            pass
        else:
            if not (request.user.is_staff or request.user.is_superuser):
                qs = qs.filter(user_id=request.user.id)
    except Exception:
        pass
    # Sorting
    try:
        # Support legacy/simple toggles: sort=oldest|newest mapped to a field inferred from context
        inferred_sort_by = None
        inferred_order = None
        if (sort or "").strip().lower() in ("oldest", "newest") and not sort_by:
            sflag = (sort or "").strip().lower()
            # Choose field based on active tab
            if (submission_status or "").lower() == "submitted":
                inferred_sort_by = "submitted"
            elif (status or "").lower() == "approved":
                inferred_sort_by = "approved"
            else:
                inferred_sort_by = "created"
            inferred_order = ("asc" if sflag == "oldest" else "desc")

        sort_key = (sort_by or inferred_sort_by or "").strip().lower()
        ord_dir = (order or inferred_order or "desc").strip().lower()
        desc = (ord_dir != "asc")
        field_map = {
            "submitted": "submitted_at",
            "approved": "approved_at",
            "created": "created_at",
            "student": "user__username",
            "item": "item__name",
        }
        if sort_key in field_map:
            fld = field_map[sort_key]
            qs = qs.order_by(("-" if desc else "") + fld)
        else:
            # Stable default: newest first
            qs = qs.order_by("-created_at")
    except Exception:
        pass

    results = []
    for r in qs:
        results.append({
            "id": r.id,
            "item": {"id": r.item_id, "name": getattr(r.item, "name", "") or ""},
            "user": {"id": r.user_id, "name": getattr(r.user, "username", None), "email": getattr(r.user, "email", None)},
            "quantity": r.quantity,
            "status": r.status,
            "created_at": r.created_at,
            "approved_at": r.approved_at,
            "return_by": r.return_by,
            "remarks": r.remarks,
            "submission_status": getattr(r, "submission_status", None),
            "submitted_at": getattr(r, "submitted_at", None),
        })
    return results

@api.post("/issue-requests/{request_id}/approve", response=IssueRequestSchema)
def approve_issue_request(request, request_id: int, payload: ApproveRequestIn):
    try:
        issue_request = IssueRequest.objects.select_related("item").get(id=request_id)
    except IssueRequest.DoesNotExist:
        return api.create_response(request, {"detail": "IssueRequest not found."}, status=404)

    if issue_request.status != 'pending':
        return api.create_response(request, {"detail": "Request already processed."}, status=400)

    # With hard-lock, consumable stock already decremented on create; nothing to change here synchronously
    item = issue_request.item

    # Determine return window
    # Compute days: prefer explicit return_days, else derive from return_by when provided
    no_of_days = 7
    return_by_iso = None
    if payload:
        if payload.return_days:
            no_of_days = int(payload.return_days)
        elif payload.return_by:
            try:
                rb = payload.return_by
                # Ensure timezone-aware datetime (frontend may send date-only or naive datetime)
                if timezone.is_naive(rb):
                    rb = timezone.make_aware(rb, timezone.get_current_timezone())
                # Clamp to end-of-day for consistency
                rb = _clamp_to_eod(rb)
                return_by_iso = rb.isoformat()
                delta = rb - timezone.now()
                no_of_days = max(1, int((delta.total_seconds() + 86399) // 86400))
            except Exception:
                no_of_days = 7
    # Persist remarks before scheduling async approval so emails include them
    if payload and payload.remarks:
        try:
            issue_request.remarks = payload.remarks
            issue_request.save(update_fields=["remarks"]) 
        except Exception:
            pass
    # Queue async approve to update DB, schedule reminders, and refresh cache
    # Pass exact return_by when provided so task uses the clamped EOD
    async_approve_issue.delay(request_id, no_of_days, True, return_by_iso)
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    try:
        _ws_emit_issue("issue_request.approved", {"id": issue_request.id})
        # Also hint item change
        _ws_emit_instrument("item.maybe_changed", {"id": item.id})
    except Exception:
        pass
    return issue_request

@api.post("/issue-requests/{request_id}/reject", response=IssueRequestSchema)
def reject_issue_request(request, request_id: int, payload: RejectRequestIn):
    issue_request = get_object_or_404(IssueRequest, id=request_id)
    if issue_request.status != 'pending':
        return api.create_response(request, {"detail": "Request already processed."}, status=400)

    reason = (payload.remarks or "") if payload else ""
    # If consumable, add back reserved qty
    with transaction.atomic():
        req = IssueRequest.objects.select_for_update().select_related("item").get(id=request_id)
        if req.status != 'pending':
            return api.create_response(request, {"detail": "Request already processed."}, status=400)
        item = Item.objects.select_for_update().get(id=req.item_id)
        if item.is_consumable:
            item.quantity += req.quantity
            item.save(update_fields=["quantity"])
        req.status = 'rejected'
        if reason:
            req.remarks = reason
        req.save(update_fields=["status", "remarks"])
    # Update caches and emit
    try:
        cache.set(
            f"instruments:item:{item.id}",
            {
                "id": item.id,
                "name": item.name,
                "category_id": item.category_id,
                "sub_category_id": item.sub_category_id,
                "quantity": item.quantity,
                "is_consumable": item.is_consumable,
                "location": item.location,
                "is_available": item.is_available,
                "min_issue_limit": item.min_issue_limit,
                "max_issue_limit": item.max_issue_limit,
                "description": item.description,
                "available_quantity": getattr(item, "available_quantity", item.quantity),
            },
            timeout=600,
        )
        _cache_invalidate_index(ITEMS_INDEX_KEY)
        _ws_emit_instrument("item.updated", {
            "id": item.id,
            "name": item.name,
            "category_id": item.category_id,
            "sub_category_id": item.sub_category_id,
            "quantity": item.quantity,
            "is_consumable": item.is_consumable,
            "location": item.location,
            "is_available": item.is_available,
            "min_issue_limit": item.min_issue_limit,
            "max_issue_limit": item.max_issue_limit,
            "description": item.description,
            "available_quantity": getattr(item, "available_quantity", item.quantity),
        })
        _ws_emit_issue("issue_request.rejected", {"id": issue_request.id})
    except Exception:
        pass
    # Dispatch rejection email (task)
    try:
        send_issue_rejected_email.delay(issue_request.id)
    except Exception:
        # Log silently; in production, consider a more robust fallback
        pass
    return issue_request


# ---------------------------
# BULK ISSUE REQUEST ACTIONS
# ---------------------------
@api.post("/issue-requests/bulk-approve", response=list[IssueRequestSchema])
def bulk_approve(request, data: BulkApproveIn):
    ids = list(set(data.ids or []))
    if not ids:
        return []
    qs = IssueRequest.objects.filter(id__in=ids, status='pending').select_related('item')
    results = []
    # Precompute clamped return_by if provided once (common for all)
    rb_iso_common = None
    rb_dt_common = None
    if data.return_by:
        try:
            rb_tmp = data.return_by
            if timezone.is_naive(rb_tmp):
                rb_tmp = timezone.make_aware(rb_tmp, timezone.get_current_timezone())
            rb_tmp = _clamp_to_eod(rb_tmp)
            rb_iso_common = rb_tmp.isoformat()
            rb_dt_common = rb_tmp
        except Exception:
            rb_iso_common = None
            rb_dt_common = None

    optimistic_results = []
    now_ts = timezone.now()
    for r in qs:
        # With hard-locks, consumable stock already reserved at creation
        # queue task
        # compute days per request
        if data.return_days:
            days = int(data.return_days)
        elif data.return_by:
            try:
                rb = rb_dt_common
                if not rb:
                    rb_local = data.return_by
                    if timezone.is_naive(rb_local):
                        rb_local = timezone.make_aware(rb_local, timezone.get_current_timezone())
                    rb = _clamp_to_eod(rb_local)
                delta = rb - timezone.now()
                days = max(1, int((delta.total_seconds() + 86399) // 86400))
            except Exception:
                days = 7
        else:
            days = 7
        # Ensure remarks are saved BEFORE dispatching async approve so consolidated emails include them
        if data.remarks:
            try:
                r.remarks = data.remarks
                r.save(update_fields=["remarks"]) 
            except Exception:
                pass
        # Suppress per-item emails in bulk mode; we'll send a consolidated mail per user
        async_approve_issue.delay(r.id, days, send_email=False, return_by_iso=rb_iso_common)
        results.append(r)

        # Build optimistic result for instant UI update (do not persist here)
        try:
            approved_at = now_ts
            if rb_dt_common:
                ret_by = rb_dt_common
            elif data.return_by:
                rb_local = data.return_by
                if timezone.is_naive(rb_local):
                    rb_local = timezone.make_aware(rb_local, timezone.get_current_timezone())
                ret_by = _clamp_to_eod(rb_local)
            else:
                ret_by = approved_at + timedelta(days=days)

            remarks_val = data.remarks if getattr(data, 'remarks', None) else (getattr(r, 'remarks', None) or "").strip() or None
            sub_status = None
            submitted_at = None
            try:
                if r.item and getattr(r.item, 'is_consumable', False):
                    sub_status = "submitted"
                    submitted_at = approved_at
                    # Compose remarks: Consumed + Submitted at line
                    try:
                        ts_txt = timezone.localtime(approved_at, ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
                    except Exception:
                        ts_txt = approved_at.isoformat()
                    line1 = "Consumed"
                    line2 = f"Submitted at {ts_txt}"
                    cur = (remarks_val or "").strip()
                    if not cur:
                        remarks_val = f"{line1}\n{line2}"
                    else:
                        add_lines = []
                        if "Consumed" not in cur:
                            add_lines.append(line1)
                        if "Submitted at" not in cur:
                            add_lines.append(line2)
                        if add_lines:
                            remarks_val = cur + "\n" + "\n".join(add_lines)
            except Exception:
                pass

            optimistic_results.append({
                "id": r.id,
                "item_id": r.item_id,
                "user_id": r.user_id,
                "quantity": r.quantity,
                "status": "approved",
                "created_at": r.created_at,
                "approved_at": approved_at,
                "return_by": ret_by,
                "remarks": remarks_val,
                "submission_status": sub_status,
                "submitted_at": submitted_at,
            })
        except Exception:
            # Fallback to raw object if optimistic shaping fails
            optimistic_results.append({
                "id": r.id,
                "item_id": r.item_id,
                "user_id": r.user_id,
                "quantity": r.quantity,
                "status": r.status,
                "created_at": r.created_at,
                "approved_at": r.approved_at,
                "return_by": r.return_by,
                "remarks": getattr(r, 'remarks', None),
                "submission_status": getattr(r, 'submission_status', None),
                "submitted_at": getattr(r, 'submitted_at', None),
            })
    _cache_invalidate_index(ITEMS_INDEX_KEY)
    try:
        _ws_emit_issue("issue_request.bulk_approved", {"ids": [r.id for r in results]})
    except Exception:
        pass
    # Trigger consolidated approval emails (delay slightly to allow tasks to commit fields)
    try:
        consolidated_ids = [r.id for r in results]
        if consolidated_ids:
            send_bulk_issue_approved_email.apply_async((consolidated_ids, getattr(data, 'return_days', None), rb_iso_common), countdown=5)
    except Exception:
        pass
    # Return optimistic payload for instant UI; actual DB updates are async
    return optimistic_results


@api.post("/issue-requests/bulk-reject", response=list[IssueRequestSchema])
def bulk_reject(request, data: BulkRejectIn):
    ids = list(set(data.ids or []))
    if not ids:
        return []
    qs = IssueRequest.objects.select_related("item").filter(id__in=ids, status='pending')
    results = []
    # Synchronously restore consumable stock
    with transaction.atomic():
        for r in qs.select_for_update():
            item = Item.objects.select_for_update().get(id=r.item_id)
            if item.is_consumable:
                item.quantity += r.quantity
                item.save(update_fields=["quantity"])
            r.status = 'rejected'
            if data.remarks:
                r.remarks = data.remarks
            r.save(update_fields=["status", "remarks"])
            results.append(r)
    # Emit updates
    try:
        for r in results:
            item = r.item
            cache.set(
                f"instruments:item:{item.id}",
                {
                    "id": item.id,
                    "name": item.name,
                    "category_id": item.category_id,
                    "sub_category_id": item.sub_category_id,
                    "quantity": item.quantity,
                    "is_consumable": item.is_consumable,
                    "location": item.location,
                    "is_available": item.is_available,
                    "min_issue_limit": item.min_issue_limit,
                    "max_issue_limit": item.max_issue_limit,
                    "description": item.description,
                    "available_quantity": getattr(item, "available_quantity", item.quantity),
                },
                timeout=600,
            )
        _cache_invalidate_index(ITEMS_INDEX_KEY)
        _ws_emit_issue("issue_request.bulk_rejected", {"ids": [r.id for r in results]})
    except Exception:
        pass
    # Dispatch consolidated rejection emails (single mail per user)
    try:
        consolidated_ids = [r.id for r in results]
        if consolidated_ids:
            send_bulk_issue_rejected_email.delay(consolidated_ids)
    except Exception:
        pass
    return results


# ---------------------------
# MESSAGES AND RETURNS
# ---------------------------
@api.get("/issue-requests/{request_id}/messages", response=list[IssueMessageSchema])
def list_issue_messages(request, request_id: int):
    # Try Redis cache first without hitting DB
    ck = _messages_cache_key(request_id)
    try:
        cached = cache.get(ck)
        if cached is not None:
            return cached
    except Exception:
        cached = None

    # Fallback to DB; validate that request exists via queryset filtering
    # and select creator to avoid N+1 lookups when building sender_name
    get_object_or_404(IssueRequest, id=request_id)
    msgs = (
        IssueMessage.objects
        .filter(issue_request_id=request_id)
        .select_related("creator")
        .order_by("-created_at")
    )
    results = []
    for m in msgs:
        results.append({
            "id": m.id,
            "issue_request_id": request_id,
            "msg_type": m.msg_type,
            "text": m.text,
            "created_at": m.created_at,
            "creator_id": m.creator_id,
            "sender_name": ("System" if getattr(m, "msg_type", "") == "system" else _user_display(getattr(m, "creator", None))),
        })
    try:
        cache.set(ck, results, timeout=MESSAGES_CACHE_TIMEOUT)
    except Exception:
        pass
    return results


@api.post("/issue-requests/{request_id}/messages", response=IssueMessageSchema)
def create_issue_message(request, request_id: int, data: IssueMessageIn):
    req = get_object_or_404(IssueRequest, id=request_id)
    msg = IssueMessage.objects.create(
        issue_request=req,
        msg_type="admin",
        text=(data.text or ""),
        creator=getattr(request, "user", None) if getattr(request, "user", None) and getattr(request.user, "is_authenticated", False) else None,
    )
    # Emit WS event
    try:
        _ws_emit_message("issue_request.message", {
            "id": msg.id,
            "issue_request_id": req.id,
            "msg_type": msg.msg_type,
            "text": msg.text,
            "created_at": msg.created_at.isoformat(),
            "creator_id": msg.creator_id,
            "sender_name": _user_display(getattr(msg, "creator", None)),
        })
    except Exception:
        pass
    # Update Redis cache
    try:
        ck = _messages_cache_key(req.id)
        entry = {
            "id": msg.id,
            "issue_request_id": req.id,
            "msg_type": msg.msg_type,
            "text": msg.text,
            "created_at": msg.created_at,
            "creator_id": msg.creator_id,
            "sender_name": _user_display(getattr(msg, "creator", None)),
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
    # Optionally enqueue email notification (HTML sending remains disabled in tasks)
    try:
        from .tasks import send_issue_message_email
        if data.notify_email:
            send_issue_message_email.delay(req.id, msg.id)
    except Exception:
        pass
    return {
        "id": msg.id,
        "issue_request_id": req.id,
        "msg_type": msg.msg_type,
        "text": msg.text,
        "created_at": msg.created_at,
        "creator_id": msg.creator_id,
    }


@api.post("/issue-requests/{request_id}/submit", response=IssueRequestSchema)
def submit_return(request, request_id: int, payload: SubmitReturnIn):
    req = get_object_or_404(IssueRequest.objects.select_related("item"), id=request_id)
    # Mark submitted
    try:
        req.submission_status = "submitted"
        req.submitted_at = timezone.now()
        # Build note text: for consumables default to explicit consumed wording
        note_text = None
        try:
            if payload and payload.message:
                note_text = payload.message
            else:
                # Build default note with IST timestamp
                try:
                    ist_now = timezone.localtime(timezone.now(), ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
                except Exception:
                    ist_now = timezone.now().strftime("%Y-%m-%d %H:%M")
                if getattr(req.item, "is_consumable", False):
                    note_text = f"Consumable submitted at {ist_now}"
                else:
                    note_text = f"Submitted at {ist_now}"
        except Exception:
            try:
                ist_now = timezone.localtime(timezone.now(), ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
            except Exception:
                ist_now = timezone.now().strftime("%Y-%m-%d %H:%M")
            note_text = (payload.message if payload else None) or f"Submitted at {ist_now}"

        if note_text:
            req.remarks = (req.remarks or "").strip()
            if req.remarks:
                req.remarks += f"\nSubmission note: {note_text}"
            else:
                req.remarks = f"Submission note: {note_text}"
        req.save(update_fields=["submission_status", "submitted_at", "remarks"])
    except Exception:
        pass
    # Create a message entry
    msg = None
    try:
        msg = IssueMessage.objects.create(
            issue_request=req,
            msg_type="system",
            text=note_text or "Item submitted",
        )
        _ws_emit_message("issue_request.message", {
            "id": msg.id,
            "issue_request_id": req.id,
            "msg_type": msg.msg_type,
            "text": msg.text,
            "created_at": msg.created_at.isoformat(),
            "creator_id": msg.creator_id,
            "sender_name": "System",
        })
        # Update Redis cache
        try:
            ck = _messages_cache_key(req.id)
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
    except Exception:
        pass
    # Optionally notify via email
    try:
        from .tasks import send_issue_message_email
        if payload and payload.notify_email and msg is not None:
            send_issue_message_email.delay(req.id, msg.id)
    except Exception:
        pass
    # Notify request update for UI without refetch
    try:
        _ws_emit_issue("issue_request.updated", {
            "id": req.id,
            "status": req.status,
            "approved_at": req.approved_at.isoformat() if req.approved_at else None,
            "return_by": req.return_by.isoformat() if req.return_by else None,
            "submission_status": getattr(req, "submission_status", None),
            "submitted_at": getattr(req, "submitted_at", None).isoformat() if getattr(req, "submitted_at", None) else None,
        })
    except Exception:
        pass

    # Refresh item snapshot and notify listeners so availability updates in UI
    try:
        item = req.item
        cache.set(
            f"instruments:item:{item.id}",
            {
                "id": item.id,
                "name": item.name,
                "category_id": item.category_id,
                "sub_category_id": item.sub_category_id,
                "quantity": item.quantity,
                "is_consumable": item.is_consumable,
                "location": item.location,
                "is_available": item.is_available,
                "min_issue_limit": item.min_issue_limit,
                "max_issue_limit": item.max_issue_limit,
                "description": item.description,
                "available_quantity": getattr(item, "available_quantity", item.quantity),
            },
            timeout=600,
        )
        _cache_invalidate_index(ITEMS_INDEX_KEY)
        _ws_emit_instrument("item.updated", {
            "id": item.id,
            "name": item.name,
            "category_id": item.category_id,
            "sub_category_id": item.sub_category_id,
            "quantity": item.quantity,
            "is_consumable": item.is_consumable,
            "location": item.location,
            "is_available": item.is_available,
            "min_issue_limit": item.min_issue_limit,
            "max_issue_limit": item.max_issue_limit,
            "description": item.description,
            "available_quantity": getattr(item, "available_quantity", item.quantity),
        })
    except Exception:
        pass
    return {
        "id": req.id,
        "item_id": req.item_id,
        "user_id": req.user_id,
        "quantity": req.quantity,
        "status": req.status,
        "created_at": req.created_at,
        "approved_at": req.approved_at,
        "return_by": req.return_by,
        "remarks": req.remarks,
        "submission_status": getattr(req, "submission_status", None),
        "submitted_at": getattr(req, "submitted_at", None),
    }