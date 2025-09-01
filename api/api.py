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
from django.http import HttpRequest
from django.core.cache import cache
api = NinjaAPI()
User = get_user_model()

SUPABASE_URL = config("SUPABASE_URL")
SUPABASE_KEY = config("SUPABASE_KEY")
SUPABASE_BUCKET = config("SUPABASE_BUCKET")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

from urllib.parse import urljoin

def get_signed_url(path: str, expires_in: int = 3600) -> str:
    res = supabase.storage.from_(SUPABASE_BUCKET).create_signed_url(path, expires_in)

    # If using Supabase Python client (like postgrest-py or storage3), check structure:
    if isinstance(res, dict):
        signed_path = res.get("signedURL")
    else:
        signed_path = getattr(res, "signedURL", None)

    if not signed_path:
        raise Exception(f"Signed URL generation failed or returned empty for path: {path}")

    return signed_path  # ✅ Already a full, signed Supabase URL


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
def login(request: HttpRequest, data: UserLoginSchema):
    email = data.email.strip().lower()
    password = data.password

    if not email.endswith("@lnmiit.ac.in"):
        return api.create_response(request, {"detail": "Only LNMIIT emails are allowed"}, status=400)

    # Try cache first
    cache_key = f"user_auth:{email}"
    user = cache.get(cache_key)

    if not user:
        user = authenticate(request, email=email, password=password)
        if user:
            cache.set(cache_key, user, timeout=60*5)  # cache 5 minutes

    if user is None:
        return api.create_response(request, {"detail": "Invalid email or password"}, status=401)

    if not user.is_active:
        return api.create_response(request, {"detail": "User account is disabled"}, status=403)

    auth_login(request, user)
    request.session.set_expiry(86400)

    return UserOutSchema.model_validate(user)

@api.post("/logout")
def logout(request):
    auth_logout(request)
    return {"message": "Logged out successfully"}

api.add_router("/auth", google_router)

@api.get("/auth/check")
def check_auth(request):
    if not request.user.is_authenticated:
        return {"authenticated": False, "user": None}

    cache_key = f"user_check:{request.user.id}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

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

    response = {"authenticated": True, "user": user_data}
    cache.set(cache_key, response, timeout=60*5)  # cache for 5 minutes
    return response


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

    # Invalidate cached data for this user
    cache.delete(f"user_check:{user.id}")
    cache.delete(f"user_full_detail:{user.id}")

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

    # Invalidate cached data for this user
    cache.delete(f"user_check:{user.id}")
    cache.delete(f"user_full_detail:{user.id}")

    return user


@api.get("/auth/full-detail")
def full_user_detail(request):
    if not request.user.is_authenticated:
        return {"authenticated": False, "user": None}

    cache_key = f"user_full_detail:{request.user.id}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    user = request.user
    user_data = {
        "id": str(user.id),
        "username": user.username or "",
        "email": user.email or "",
        "role": user.role or "",
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "profile_picture": getattr(user, "profile_picture", "") or "",
        "date_joined": str(user.date_joined),
        "last_login": str(user.last_login),
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "is_staff": user.is_staff,
    }

    # Role-specific info
    if user.role == "student":
        try:
            profile = StudentProfile.objects.get(user=user)
            user_data.update({
                "roll_number": profile.roll_number or "",
                "department": profile.department or "",
                "year": profile.year or ""
            })
        except StudentProfile.DoesNotExist:
            user_data.update({"roll_number": "", "department": "", "year": ""})

    elif user.role == "faculty":
        try:
            profile = FacultyProfile.objects.get(user=user)
            user_data["department"] = profile.department or ""
        except FacultyProfile.DoesNotExist:
            user_data["department"] = ""

    elif user.role == "staff":
        try:
            profile = StaffProfile.objects.get(user=user)
            user_data["department"] = profile.department or ""
        except StaffProfile.DoesNotExist:
            user_data["department"] = ""

    response = {"authenticated": True, "user": user_data}
    cache.set(cache_key, response, timeout=60*5)  # 5 min cache
    return response


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

    return api.create_response(
        request,
        ExcelImportResponse(success_count=success, failed=failed),
        status=201
    )


#File Handling
from django.core.cache import cache

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

    # Cache metadata for this file (5 minutes)
    cache.set(f"file_meta:{uploaded.id}", uploaded, timeout=300)

    return uploaded


@api.post("/upload")
def upload_file(request, file: NinjaUploadedFile):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)

    year = request.POST.get("year") or request.POST.get("year[]") or None

    try:
        supabase_path = upload_to_supabase(file, file.name)
    except Exception as e:
        return api.create_response(request, {"detail": f"Upload failed: {str(e)}"}, status=500)

    uploaded = UploadedFileModel.objects.create(
        user=request.user,
        file=None,
        filename=file.name,
        size=file.size,
        year=year,
        cdn_url=supabase_path,
    )

    # Cache metadata
    cache.set(f"file_meta:{uploaded.id}", uploaded, timeout=300)

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
        raise HttpError(401, "Authentication required")

    cache_key = f"uploaded_files:{request.user.id}"
    cached_files = cache.get(cache_key)
    if cached_files:
        return cached_files

    files = (
        UploadedFileModel.objects.all()
        if request.user.role in ["admin", "faculty"]
        else UploadedFileModel.objects.filter(user=request.user)
    ).order_by("-uploaded_at")

    result = []
    for f in files:
        result.append({
            "id": f.id,
            "user": f.user_id,
            "filename": f.filename,
            "size": f.size,
            "uploaded_at": f.uploaded_at,
            "cdn_url": f.cdn_url or "",
            "year": f.year or "",
        })

    # Cache the list for 5 minutes
    cache.set(cache_key, result, timeout=300)
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

    try:
        path = uploaded_file.cdn_url
        res = supabase.storage.from_(SUPABASE_BUCKET).remove([path])
        if hasattr(res, "error") and res.error:
            print(f"Supabase deletion error: {res.error.message}")
    except Exception as e:
        print(f"Supabase removal failed: {e}")

    uploaded_file.delete()

    # Invalidate cache
    cache.delete(f"file_meta:{file_id}")
    cache.delete(f"uploaded_files:{request.user.id}")

    return {"success": True, "detail": "File deleted successfully."}



@api.get("/get-signed-url/{filename}")
def get_signed_url_view(request, filename: str):
    cache_key = f"signed_url:{filename}"
    url = cache.get(cache_key)

    if not url:
        try:
            url = get_signed_url(filename)
            cache.set(cache_key, url, timeout=60*5)  # cache 5 minutes
        except Exception as e:
            return api.create_response(request, {"detail": str(e)}, status=500)

    return {"url": url}

import requests
from django.http import StreamingHttpResponse, HttpResponse
from ninja.errors import HttpError
from ninja.security import django_auth

@api.get("/secure-stream", auth=django_auth)
def secure_stream(request, path: str):
    if not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    cache_key = f"signed_stream:{path}"
    signed_url = cache.get(cache_key)

    if not signed_url:
        try:
            signed_url = get_signed_url(path, expires_in=60)
            cache.set(cache_key, signed_url, timeout=50)  # cache for 50 seconds
        except Exception as e:
            print(f"[ERROR] Failed to generate signed URL for {path}: {e}")
            return HttpResponse("File could not be streamed.", status=500)

    try:
        response = requests.get(signed_url, stream=True, timeout=10)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch file from Supabase (status: {response.status_code})")

        content_type = response.headers.get("Content-Type", "application/octet-stream")
        content_disposition = f'inline; filename="{path.split("_", 1)[-1]}"'

        return StreamingHttpResponse(
            response.iter_content(chunk_size=8192),
            content_type=content_type,
            headers={"Content-Disposition": content_disposition},
        )

    except Exception as e:
        print(f"[ERROR] Stream failed for {path}: {e}")
        return HttpResponse("File could not be streamed.", status=500)
