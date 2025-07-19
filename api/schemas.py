from ninja import Schema, ModelSchema
from pydantic import EmailStr, constr , ConfigDict
from typing import Annotated, Literal, Optional
from .models import CustomUser
from ninja import Schema, ModelSchema
from pydantic import EmailStr, constr , BaseModel
from typing import Annotated, Literal, Optional, List, Dict
from .models import CustomUser
from .models import UploadedFile as UploadedFileModel
import datetime

# ✅ Output schema for uploaded files
class UploadedFileOutSchema(BaseModel):
    id: int
    user: int
    filename: str
    size: int
    uploaded_at: datetime.datetime
    cdn_url: Optional[str]
    year: Optional[str]

    class Config:
        from_attributes = True


# ✅ Input schema for file upload (if you want to accept extra fields)
class UploadedFileInSchema(Schema):
    filename: str
    size: int
    cdn_url: Optional[str] = None  # Public Supabase or external URL
    year: Optional[str] = None


# ...existing code...
# ✅ Output schema — never includes sensitive data
class UserOutSchema(BaseModel):
    id: int
    username: str
    email: str
    role: str
    date_joined: datetime.datetime
    is_active: bool

    model_config = ConfigDict(from_attributes=True)

# ✅ Input schema for user signup
class UserSignupSchema(Schema):
    username: str
    email: EmailStr
    password: Annotated[str, constr(min_length=8)]  # strong password validation

# ✅ Input schema for login
class UserLoginSchema(Schema):
    email: EmailStr
    password: str

# ✅ Input schema for admin to create a user
class AdminCreateUserSchema(Schema):
    username: str
    email: EmailStr
    password: Annotated[str, constr(min_length=8)]
    role: Literal['student', 'faculty', 'staff', 'admin']
    roll_number: Optional[str] = None  # Only required for student
    department: Optional[str] = None   # Required for student, faculty, staff

class UserUpdateSchema(Schema):
    username: Optional[str]
    department: Optional[str]
    roll_number: Optional[str] = None

from ninja import Schema
from pydantic import BaseModel
from typing import List, Optional


class FailedRow(BaseModel):
    row: int
    error: str

    class Config:
        from_attributes = True  # Enables model creation from dicts or ORM objects


class ExcelImportResponse(BaseModel):
    success_count: int
    failed: List[FailedRow]

    class Config:
        from_attributes = True
