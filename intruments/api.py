from ninja import NinjaAPI
from django.contrib.auth import get_user_model
from django.utils.timezone import make_aware
from django.db import IntegrityError
from .models import Item, Category, SubCategory , IssueRequest
from .schemas import (
    ItemSchema, ItemIn,
    CategorySchema, SubCategorySchema,
    CategoryIn, SubCategoryIn,IssueRequestIn,IssueRequestSchema
)
from django.shortcuts import get_object_or_404
import datetime

api = NinjaAPI(urls_namespace="instruments")
User = get_user_model()

# ──────── ITEM ROUTES ───────── #

@api.get("/items/{item_id}", response=ItemSchema)
def get_item(request, item_id: int):
    """
    View details of a single item.
    """
    try:
        item = Item.objects.get(id=item_id)
        return item
    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)

@api.put("/items/{item_id}", response=ItemSchema)
def update_item(request, item_id: int, data: ItemIn):
    """
    Modify an existing item.
    """
    try:
        item = Item.objects.get(id=item_id)

        # ✅ Fetch category and subcategory by ID
        category_obj = get_object_or_404(Category, id=data.category_id)
        subcategory_obj = get_object_or_404(SubCategory, id=data.sub_category_id)

        item.category = category_obj
        item.sub_category = subcategory_obj
        item.name = data.name
        item.serial_number = data.serial_number
        item.cost = data.cost
        item.quantity = data.quantity
        item.gst_number = data.gst_number
        item.buyer_name = data.buyer_name
        item.buyer_email = data.buyer_email
        item.purchase_date = make_aware(data.purchase_date) if data.purchase_date.tzinfo is None else data.purchase_date
        item.bill_number = data.bill_number
        item.remarks = data.remarks

        item.save()
        return item

    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)
    except Category.DoesNotExist:
        return api.create_response(request, {"detail": "Invalid category_id"}, status=400)
    except SubCategory.DoesNotExist:
        return api.create_response(request, {"detail": "Invalid sub_category_id"}, status=400)
    except IntegrityError as e:
        if "unique constraint" in str(e).lower():
            return api.create_response(request, {
                "detail": "An item with the same serial number and bill number already exists in this category."
            }, status=400)
        return api.create_response(request, {"detail": "Database error during update."}, status=500)


@api.delete("/items/{item_id}")
def delete_item(request, item_id: int):
    """
    Delete an item.
    """
    try:
        item = Item.objects.get(id=item_id)
        item.delete()
        return {"success": True}
    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)


from ninja import Schema

class ItemIssueRequest(Schema):
    quantity: int

@api.post("/items/{item_id}/issue", response=ItemSchema)
def issue_item(request, item_id: int, data: ItemIssueRequest):
    """
    Deducts the issued quantity from the item's quantity.
    """
    try:
        item = Item.objects.get(id=item_id)
        available = int(item.quantity)
        requested = int(data.quantity)
        if requested <= 0:
            return api.create_response(request, {"detail": "Quantity must be greater than 0."}, status=400)
        if requested > available:
            return api.create_response(request, {"detail": "You can't issue more than available quantity."}, status=400)
        item.quantity = str(available - requested)
        item.save()
        return item
    except Item.DoesNotExist:
        return api.create_response(request, {"detail": "Item not found"}, status=404)
    except Exception as e:
        return api.create_response(request, {"detail": f"Error: {str(e)}"}, status=500)

@api.get("/items", response=list[ItemSchema])
def list_items(request, category: int = None, subcategory: int = None):
    items = Item.objects.all()
    if category:
        items = items.filter(category_id=category)
    if subcategory:
        items = items.filter(sub_category_id=subcategory)
    return items

@api.post("/items", response=ItemSchema)
def create_item(request, item: ItemIn):
    try:
        # ✅ Fetch category and subcategory using ID
        category_obj = get_object_or_404(Category, id=item.category_id)
        subcategory_obj = get_object_or_404(SubCategory, id=item.sub_category_id)


        # ✅ Ensure datetime is timezone-aware
        aware_date = (
            make_aware(item.purchase_date)
            if item.purchase_date.tzinfo is None
            else item.purchase_date
        )

        # ✅ Create the item
        new_item = Item.objects.create(
            category=category_obj,
            sub_category=subcategory_obj,
            name=item.name,
            serial_number=item.serial_number,
            cost=item.cost,
            quantity=item.quantity,
            gst_number=item.gst_number,
            buyer_name=item.buyer_name,
            buyer_email=item.buyer_email,
            purchase_date=aware_date,
            bill_number=item.bill_number,
            remarks=item.remarks,
        )

        return new_item

    except Category.DoesNotExist:
        return api.create_response(request, {"detail": "Invalid category_id"}, status=400)
    except SubCategory.DoesNotExist:
        return api.create_response(request, {"detail": "Invalid sub_category_id"}, status=400)
    except IntegrityError as e:
        if "unique" in str(e).lower():
            return api.create_response(
                request,
                {"detail": "Item with same serial and bill number already exists."},
                status=400,
            )
        return api.create_response(request, {"detail": "Database error occurred."}, status=500)
    except Exception as e:
        return api.create_response(request, {"detail": f"Unexpected error: {str(e)}"}, status=500)


# ──────── CATEGORY ROUTES ───────── #

@api.get("/categories", response=list[CategorySchema])
def list_categories(request):
    return Category.objects.all()

@api.post("/categories", response=CategorySchema)
def create_category(request, data: CategoryIn):
    if Category.objects.filter(name__iexact=data.name).exists():
        return api.create_response(
            request,
            {"detail": "Category already exists."},
            status=400
        )
    return Category.objects.create(name=data.name)

# ──────── SUBCATEGORY ROUTES ───────── #

@api.get("/subcategories", response=list[SubCategorySchema])
def list_subcategories(request, category_id: int):
    return SubCategory.objects.filter(category_id=category_id)

@api.post("/subcategories", response=SubCategorySchema)
def create_subcategory(request, data: SubCategoryIn):
    if SubCategory.objects.filter(name__iexact=data.name, category_id=data.category_id).exists():
        return api.create_response(
            request,
            {"detail": "Subcategory already exists for this category."},
            status=400
        )
    return SubCategory.objects.create(name=data.name, category_id=data.category_id)

@api.post("/issue-requests/", response=IssueRequestSchema)
def create_issue_request(request, data: IssueRequestIn):
    """
    Create an issue request (pending approval).
    """
    user = request.user
    item = get_object_or_404(Item, id=data.item_id)
    if data.quantity <= 0:
        return api.create_response(request, {"detail": "Quantity must be greater than 0."}, status=400)
    if data.quantity > int(item.quantity):
        return api.create_response(request, {"detail": "Requested quantity exceeds available."}, status=400)
    issue_request = IssueRequest.objects.create(
        item=item,
        user=user,
        quantity=data.quantity,
        remarks=data.remarks,
        status='pending'
    )
    return issue_request

@api.get("/issue-requests/", response=list[IssueRequestSchema])
def list_issue_requests(request, status: str = None):
    qs = IssueRequest.objects.select_related("item", "user")
    if status:
        qs = qs.filter(status=status)
    return qs

@api.post("/issue-requests/{request_id}/approve", response=IssueRequestSchema)
def approve_issue_request(request, request_id: int):
    """
    Admin approves an issue request and decreases item quantity.
    """
    issue_request = get_object_or_404(IssueRequest, id=request_id)
    if issue_request.status != 'pending':
        return api.create_response(request, {"detail": "Request already processed."}, status=400)
    item = issue_request.item
    if issue_request.quantity > int(item.quantity):  # <--- STOCK CHECK IS HERE
        return api.create_response(request, {"detail": "Not enough quantity available."}, status=400)
    item.quantity = str(int(item.quantity) - issue_request.quantity)
    item.save()
    issue_request.status = 'approved'
    issue_request.save()
    return issue_request

@api.post("/issue-requests/{request_id}/reject", response=IssueRequestSchema)
def reject_issue_request(request, request_id: int):
    """
    Admin rejects an issue request.
    """
    issue_request = get_object_or_404(IssueRequest, id=request_id)
    if issue_request.status != 'pending':
        return api.create_response(request, {"detail": "Request already processed."}, status=400)
    issue_request.status = 'rejected'
    issue_request.save()
    return issue_request