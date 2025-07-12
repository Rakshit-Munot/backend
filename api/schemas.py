from ninja import Schema, ModelSchema
from pydantic import EmailStr, constr
from typing import Annotated, Literal, Optional
from .models import CustomUser
from ninja import Schema, ModelSchema
from pydantic import EmailStr, constr
from typing import Annotated, Literal, Optional, List, Dict
from .models import CustomUser
from .models import UploadedFile as UploadedFileModel

# ✅ Output schema for uploaded files
class UploadedFileOutSchema(ModelSchema):
    class Config:
        model = UploadedFileModel
        model_fields = [
            'id',
            'user',
            'filename',
            'size',
            'uploaded_at',
            'cdn_url',
            'year',
        ]

# ✅ Input schema for file upload (if you want to accept extra fields)
class UploadedFileInSchema(Schema):
    filename: str
    size: int
    cdn_url: Optional[str] = None  # Public Supabase or external URL
    year: Optional[str] = None


# ...existing code...
# ✅ Output schema — never includes sensitive data
class UserOutSchema(ModelSchema):
    class Config:
        model = CustomUser
        model_fields = ['id', 'username', 'email', 'role', 'date_joined', 'is_active']

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


from ninja.files import UploadedFile
from ninja import Schema

# ✅ Excel Import Response Schema
class ExcelImportResponse(Schema):
    success_count: int
    failed: List[Dict[str, str]]  # e.g., [{"row": 3, "error": "Email already exists"}]
