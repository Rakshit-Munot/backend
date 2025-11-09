#  Pros:
# Full control and transparency:

# You define each field, type, and transformation explicitly.

# Easier to debug and extend.

# Custom serialization & computed fields:

# Add methods or properties.

# Transform ORM objects however you want.

# Great for complex/nested schemas:

# Ideal if you have nested objects like:

# python
# Copy
# Edit
# category: CategorySchema
# sub_category: SubCategorySchema
# Flexible with external data sources:

# Useful if you're not always returning Django ORM objects (e.g., API integrations).

# ❌ Cons:
# More verbose and repetitive:

# You need to define every field manually.

# Needs from_attributes = True:

# Otherwise, it won’t work with Django ORM models by default.

# ✅ Pros:
# Quick and automatic:

# Auto-generates schema fields directly from Django models.

# Saves you from repeating field definitions.

# Less boilerplate:

# python
# Copy
# Edit
# class ItemSchema(ModelSchema):
#     class Config:
#         model = Item
#         model_fields = '__all__'
# vs.

# python
# Copy
# Edit
# class ItemSchema(BaseModel):
#     id: int
#     name: str
#     ...
# Keeps schemas in sync with models — if the model changes, you only update in one place.

# ❌ Cons:
# Less control/flexibility:

# Harder to exclude or rename fields unless using model_fields = [...] explicitly.

# You can’t easily add computed fields (like full_name = first + last) or tweak serialization behavior.

# Relies heavily on internal magic:

# You don’t see what’s really happening unless you dig in.

# Limited customization for nested models:

# For deeply nested relations or complex serialization, it's harder to control formatting.

from ninja import Schema
from typing import Optional, List
from datetime import datetime


class CategoryIn(Schema):
    name: str


class CategorySchema(Schema):
    id: int
    name: str


class SubCategoryIn(Schema):
    name: str
    category_id: int


class SubCategorySchema(Schema):
    id: int
    name: str
    category_id: int


class ItemIn(Schema):
    category_id: int
    sub_category_id: Optional[int] = None
    name: str
    quantity: int
    is_consumable: bool = True
    is_available: bool = True
    location: Optional[str] = ""
    min_issue_limit: int = 1
    max_issue_limit: int = 1
    description: Optional[str] = ""


class ItemSchema(Schema):
    id: int
    name: str
    category_id: int
    sub_category_id: Optional[int]
    quantity: int
    is_consumable: bool
    location: Optional[str]
    is_available: bool
    min_issue_limit: int
    max_issue_limit: int
    description: Optional[str]
    available_quantity: int


class IssueRequestIn(Schema):
    item_id: int
    quantity: int
    remarks: Optional[str] = None


class IssueRequestSchema(Schema):
    id: int
    item_id: int
    user_id: int
    quantity: int
    status: str
    created_at: datetime
    approved_at: Optional[datetime]
    return_by: Optional[datetime]
    remarks: Optional[str]


class ItemSummary(Schema):
    id: int
    name: str


class UserSummary(Schema):
    id: int
    name: Optional[str] = None
    email: Optional[str] = None


class IssueRequestListSchema(Schema):
    id: int
    item: ItemSummary
    user: UserSummary
    quantity: int
    status: str
    created_at: datetime
    approved_at: Optional[datetime]
    return_by: Optional[datetime]
    remarks: Optional[str]


class ApproveRequestIn(Schema):
    return_days: Optional[int] = None
    return_by: Optional[datetime] = None
    remarks: Optional[str] = None


class RejectRequestIn(Schema):
    remarks: Optional[str] = None


class BulkApproveIn(Schema):
    ids: List[int]
    return_days: Optional[int] = None
    return_by: Optional[datetime] = None
    remarks: Optional[str] = None


class BulkRejectIn(Schema):
    ids: List[int]
    remarks: str


class UserCreateIn(Schema):
    username: str
    password: str
    email: Optional[str] = None
    is_staff: bool = False


class UserSchema(Schema):
    id: int
    username: str
    email: Optional[str]
    is_staff: bool