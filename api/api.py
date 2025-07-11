import pandas as pd
from ninja import NinjaAPI
from ninja.responses import Response
from django.contrib.auth import get_user_model, authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from ninja.files import UploadedFile
from ninja.files import UploadedFile as NinjaUploadedFile
from .schemas import (
    UserSignupSchema,
    UserOutSchema,
    UserLoginSchema,
    AdminCreateUserSchema,
    UserUpdateSchema,
    ExcelImportResponse,
    UploadedFileOutSchema,
)
from .api_google import router as google_router
from .dependencies import admin_only as admin_required
from api.models import StudentProfile, FacultyProfile, StaffProfile, UploadedFile as UploadedFileModel
from .schemas import UploadedFileInSchema
from .utils import upload_to_supabase
from decouple import config
from supabase import create_client

api = NinjaAPI()
User = get_user_model()

SUPABASE_URL = config("SUPABASE_URL")
SUPABASE_KEY = config("SUPABASE_KEY")
SUPABASE_BUCKET = config("SUPABASE_BUCKET")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_signed_url(path: str, expires_in: int = 3600) -> str:
    res = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(path, expires_in)
    if hasattr(res, "error") and res.error:
        raise Exception(f"Signed URL generation failed: {res.error.message}")
    return res.get("signedURL")

@api.get("/users", response=list[UserOutSchema])
def list_users(request):
    return User.objects.all().order_by("username")

@api.post("/signup", response=UserOutSchema)
def create_user(request, data: UserSignupSchema):
    email = data.email.strip().lower()
    username = data.username.strip()
    if not email.endswith("@lnmiit.ac.in"):
        return api.create_response(request, {"detail": "Only LNMIIT emails are allowed"}, status=400)

    if User.objects.filter(email=email).exists():
        return api.create_response(request, {"detail": "Email already exists"}, status=400)

    try:
        validate_password(data.password)
    except ValidationError as e:
        return api.create_response(request, {"detail": e.messages}, status=400)

    user = User.objects.create_user(
        email=email,
        username=username,
        password=data.password,
        role="student"
    )
    return user

@api.post("/login", response=UserOutSchema)
def login(request, data: UserLoginSchema):
    email = data.email.strip().lower()
    password = data.password

    if not email.endswith("@lnmiit.ac.in"):
        return api.create_response(request, {"detail": "Only LNMIIT emails are allowed"}, status=400)
    user = authenticate(request, email=email, password=password)
    if user is None:
        return api.create_response(request, {"detail": "Invalid email or password"}, status=401)

    if not user.is_active:
        return api.create_response(request, {"detail": "User account is disabled"}, status=403)

    auth_login(request, user)
    request.session.set_expiry(3600 * 24)
    return user

@api.post("/logout")
def logout(request):
    auth_logout(request)
    return {"message": "Logged out successfully"}

api.add_router("/auth", google_router)

@api.get("/auth/check")
def check_auth(request):
    if request.user.is_authenticated:
        user_data = {
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
            "role": request.user.role,
        }

        if request.user.role == "student":
            try:
                profile = StudentProfile.objects.get(user=request.user)
                user_data["roll_number"] = profile.roll_number
            except StudentProfile.DoesNotExist:
                user_data["roll_number"] = None

        return {"authenticated": True, "user": user_data}

    return {"authenticated": False, "user": None}

@api.post("/admin/create-user", response=UserOutSchema)
@admin_required
def admin_create_user(request, data: AdminCreateUserSchema):
    email = data.email.strip().lower()
    username = data.username.strip()

    if User.objects.filter(email=email).exists():
        return api.create_response(request, {"detail": "Email already exists"}, status=400)

    try:
        validate_password(data.password)
    except ValidationError as e:
        return api.create_response(request, {"detail": e.messages}, status=400)

    if data.role == "student":
        if not data.roll_number or not data.department:
            return api.create_response(request, {"detail": "Student must have roll_number and department"}, status=400)
        if StudentProfile.objects.filter(roll_number=data.roll_number).exists():
            return api.create_response(request, {"detail": "Roll number already exists"}, status=400)

    if data.role in ["faculty", "staff"] and not data.department:
        return api.create_response(request, {"detail": f"{data.role.capitalize()} must have a department"}, status=400)

    user = User.objects.create_user(
        email=email,
        username=username,
        password=data.password,
        role=data.role
    )

    if data.role == "student":
        StudentProfile.objects.create(user=user, roll_number=data.roll_number, department=data.department)
    elif data.role == "faculty":
        FacultyProfile.objects.create(user=user, department=data.department)
    elif data.role == "staff":
        StaffProfile.objects.create(user=user, department=data.department)

    return user

@api.put("/users/{user_id}/update", response=UserOutSchema)
@admin_required
def update_user(request, user_id: int, data: UserUpdateSchema):
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return api.create_response(request, {"detail": "User not found"}, status=404)

    if data.username:
        user.username = data.username.strip()

    if data.email:
        email = data.email.strip().lower()
        if User.objects.exclude(id=user_id).filter(email=email).exists():
            return api.create_response(request, {"detail": "Email already in use"}, status=400)
        user.email = email

    user.save()

    if user.role == "student":
        profile, _ = StudentProfile.objects.get_or_create(user=user)
        if data.roll_number:
            if not request.user.is_superuser:
                return api.create_response(request, {"detail": "Only admin can change roll number."}, status=403)
            profile.roll_number = data.roll_number
            user.username = data.roll_number
            profile.department = data.roll_number[2:5].upper()

        if data.department:
            profile.department = data.department

        profile.save()
        user.save()

    elif user.role == "faculty":
        profile, _ = FacultyProfile.objects.get_or_create(user=user)
        if data.department:
            profile.department = data.department
            profile.save()

    elif user.role == "staff":
        profile, _ = StaffProfile.objects.get_or_create(user=user)
        if data.department:
            profile.department = data.department
            profile.save()

    return user

@api.post("/admin/import-users", response=ExcelImportResponse)
@admin_required
def import_users(request, file: UploadedFile) -> Response:
    df = pd.read_excel(file.file, engine='openpyxl')
    df.columns = df.columns.str.strip().str.lower()

    required_columns = {"email", "role", "username", "password"}
    missing = required_columns - set(df.columns)
    if missing:
        return api.create_response(request, {"detail": f"Missing required columns: {', '.join(missing)}"}, status=400)

    success = 0
    failed = []

    for index, row in df.iterrows():
        try:
            email = str(row["email"]).strip().lower()
            username = str(row["username"]).strip()
            role = str(row["role"]).strip().lower()
            password = str(row["password"])
            picture = row.get("picture", "") or None
            department = row.get("department", "")
            roll_number = row.get("roll_number", "")

            if not email.lower().endswith("@lnmiit.ac.in"):
                raise ValueError("Only LNMIIT emails are allowed")

            if User.objects.filter(email=email).exists():
                raise ValueError("Email already exists")

            if role == "student":
                if not roll_number or not department:
                    raise ValueError("Student must have roll_number and department")
                if StudentProfile.objects.filter(roll_number=roll_number).exists():
                    raise ValueError("Roll number already exists")

            validate_password(password)

            user = User.objects.create_user(
                email=email,
                username=username,
                password=password,
                role=role,
                profile_picture=picture
            )

            if role == "student":
                StudentProfile.objects.create(user=user, roll_number=roll_number, department=department)
            elif role == "faculty":
                FacultyProfile.objects.create(user=user, department=department)
            elif role == "staff":
                StaffProfile.objects.create(user=user, department=department)

            success += 1

        except Exception as e:
            failed.append({"row": int(index) + 2, "error": str(e)})

    return api.create_response(request, ExcelImportResponse(success_count=success, failed=failed), status=201)

@api.post("/save-file-meta", response=UploadedFileOutSchema)
def save_file_meta(request, data: UploadedFileInSchema):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)

    uploaded = UploadedFileModel.objects.create(
        user=request.user,
        filename=data.filename,
        size=data.size,
        year=data.year,
        cdn_url=data.cdn_url,
    )

    return uploaded

@api.post("/upload")
def upload_file(request, file: NinjaUploadedFile):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)

    user = request.user
    year = request.POST.get("year") or request.POST.get("year[]")

    try:
        cdn_url = upload_to_supabase(file, file.name)
    except Exception as e:
        return api.create_response(request, {"detail": str(e)}, status=500)

    uploaded = UploadedFileModel.objects.create(
        user=user,
        file=None,
        filename=file.name,
        size=file.size,
        year=year,
        cdn_url=cdn_url
    )

    return {
        "success": True,
        "filename": uploaded.filename,
        "url": uploaded.cdn_url,
        "size": uploaded.size,
        "id": uploaded.id,
        "uploaded_at": uploaded.uploaded_at,
        "year": uploaded.year,
    }

@api.get("/uploaded-files", response=list[UploadedFileOutSchema])
def list_uploaded_files(request):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)

    if request.user.role in ["admin", "faculty"]:
        files = UploadedFileModel.objects.all().order_by('-uploaded_at')
    else:
        files = UploadedFileModel.objects.filter(user=request.user).order_by('-uploaded_at')

    result = []
    for f in files:
        try:
            path = f.cdn_url.split("/object/public/")[-1]
            signed_url = get_signed_url(path)
        except:
            signed_url = f.cdn_url

        result.append({
            "id": f.id,
            "filename": f.filename,
            "cdn_url": signed_url,
            "size": f.size,
            "year": f.year
        })

    return result

@api.delete("/uploaded-files/{file_id}/delete")
def delete_uploaded_file(request, file_id: int):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)

    if request.user.role not in ["admin", "faculty"]:
        return api.create_response(request, {"detail": "Permission denied"}, status=403)

    try:
        uploaded_file = UploadedFileModel.objects.get(id=file_id)
    except UploadedFileModel.DoesNotExist:
        return api.create_response(request, {"detail": "File not found"}, status=404)

    if uploaded_file.file:
        uploaded_file.file.delete(save=False)

    uploaded_file.delete()
    return {"success": True, "detail": "File deleted successfully."}
