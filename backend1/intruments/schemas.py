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

from ninja import ModelSchema, Schema
from pydantic import BaseModel, Field, EmailStr
from .models import Item,IssueRequest
from typing import Optional
import datetime
# ✅ Item schema (used for both input and output)

from ninja import ModelSchema, Schema
from pydantic import BaseModel, Field
from .models import Category, SubCategory

# Category Schema
class CategorySchema(ModelSchema):
    class Config:
        model = Category
        model_fields = ['id', 'name']

class CategoryIn(BaseModel):
    name: str = Field(..., max_length=100)


# SubCategory Schema
class SubCategorySchema(ModelSchema):
    class Config:
        model = SubCategory
        model_fields = ['id', 'name', 'category']
        depth = 1

class SubCategoryIn(BaseModel):
    name: str = Field(..., max_length=100)
    category_id: int

class ItemSchema(BaseModel):
    id: int
    category: CategorySchema
    sub_category: SubCategorySchema
    name: str
    serial_number: str
    cost: float
    quantity: int
    gst_number: str
    buyer_name: str
    buyer_email: EmailStr
    purchase_date: datetime.datetime
    bill_number: Optional[str]
    remarks: Optional[str]

    class Config:
        from_attributes = True  # ✅ Required for Django ORM


class ItemIn(BaseModel):
    category_id: int
    sub_category_id: int
    name: str = Field(..., max_length=100)
    serial_number: str = Field(..., max_length=100)
    cost: float = Field(..., gt=0)
    quantity: int = Field(..., ge=0)
    gst_number: str = Field(..., max_length=15)
    buyer_name: str = Field(..., max_length=100)
    buyer_email: EmailStr
    purchase_date: datetime.datetime
    bill_number: Optional[str] = Field(default=None, max_length=50)
    remarks: Optional[str] = Field(default="")


class IssueRequestIn(Schema):
    item_id: int
    quantity: int
    remarks: str = None

class SimpleUserSchema(Schema):
    id: int
    name: str = ""
    email: EmailStr = ""

class IssueRequestSchema(Schema):
    id: int
    item: ItemSchema
    user: SimpleUserSchema
    quantity: int
    status: str
    created_at: datetime.datetime
    remarks: str = None