import pandas as pd
from ninja import NinjaAPI
from ninja import Schema
from ninja.responses import Response
from django.contrib.auth import get_user_model, authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from ninja.files import UploadedFile
from ninja.files import UploadedFile as NinjaUploadedFile
from django.utils import timezone
import secrets
from .schemas import (
    UserSignupSchema,
    UserOutSchema,
    UserLoginSchema,
    AdminCreateUserSchema,
    UserUpdateSchema,
    ExcelImportResponse,
    UploadedFileOutSchema,
    UserOut,
)
from .api_google import router as google_router
from .dependencies import admin_only as admin_required
from api.models import CustomUser,StudentProfile, FacultyProfile, StaffProfile, UploadedFile as UploadedFileModel
from .schemas import UploadedFileInSchema
from .utils import upload_to_supabase
from decouple import config
from supabase import create_client
from django.http import HttpRequest
from django.core.cache import cache
from asgiref.sync import async_to_sync
from ninja import Form
from typing import List, Optional
import os
import uuid
from django.conf import settings
import hmac
import hashlib
import base64
import time

api = NinjaAPI()

# Helpers for short-lived public viewing tokens (for Google Viewer)
def _public_token_secret() -> str:
    return getattr(settings, "PUBLIC_FILE_TOKEN_SECRET", None) or os.environ.get("PUBLIC_FILE_TOKEN_SECRET", "dev-insecure-secret")


def _sign_public_token(bill_id: int, exp: int) -> str:
    payload = f"{bill_id}.{exp}".encode()
    secret = _public_token_secret().encode()
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
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


# -----------------------------
# Bills caching helpers
# -----------------------------
from django.core.cache import cache as _dj_cache

_BILLS_VER_KEY = "bills:cache:version"


def _bills_cache_version() -> int:
    try:
        ver = _dj_cache.get(_BILLS_VER_KEY)
        if not isinstance(ver, int):
            _dj_cache.set(_BILLS_VER_KEY, 1, None)
            return 1
        return ver
    except Exception:
        return 1


def _bills_bump_version():
    try:
        # Some backends may not support incr before set
        if _dj_cache.get(_BILLS_VER_KEY) is None:
            _dj_cache.set(_BILLS_VER_KEY, 1, None)
        else:
            _dj_cache.incr(_BILLS_VER_KEY)
    except Exception:
        # Best-effort
        _dj_cache.set(_BILLS_VER_KEY, int(time.time()), None)


def _bills_cache_key(fy: Optional[str], page: int, limit: int) -> str:
    ver = _bills_cache_version()
    return f"bills:v{ver}:fy={fy or 'ALL'}:p={page}:l={limit}"


def _bills_years_cache_key() -> str:
    ver = _bills_cache_version()
    return f"bills:v{ver}:years"


@api.get("/users", response=list[UserOutSchema])
def list_users(request):
    return User.objects.all().order_by("username")

@api.post("/signup", response=UserOutSchema)
def create_user(request, data: UserSignupSchema):
    email = data.email.strip().lower()
    username = data.username.strip()
    # TEMP DISABLED: Domain restriction to allow testing email sending with non-LNMIIT accounts
    # if not email.endswith("@lnmiit.ac.in"):
    #     return api.create_response(request, {"detail": "Only LNMIIT emails are allowed"}, status=400)

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

    # TEMP DISABLED: Domain restriction to allow testing email sending with non-LNMIIT accounts
    # if not email.endswith("@lnmiit.ac.in"):
    #     return api.create_response(request, {"detail": "Only LNMIIT emails are allowed"}, status=400)

    # Always authenticate — do NOT use cached user for login
    user = authenticate(request, email=email, password=password)

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


# ========================
# Auth: Forgot/Reset Password
# ========================

class ForgotPasswordIn(Schema):
    email: str


class ResetPasswordIn(Schema):
    uid: str
    token: str
    new_password: str


@api.post("/auth/forgot-password")
def forgot_password(request, payload: ForgotPasswordIn):
    """Initiate password reset by sending a tokenized link to the user's email.
    Always respond success-like to avoid email enumeration.
    """
    try:
        email = (payload.email or "").strip().lower()
        # Find active user by email
        try:
            user = User.objects.get(email=email, is_active=True)
        except User.DoesNotExist:
            # Do not reveal whether the email exists
            return {"message": "If an account exists, you will receive an email shortly."}

        token = PasswordResetTokenGenerator().make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        frontend_base = config('FRONTEND_URL', default='http://localhost:3000').rstrip('/')
        reset_url = f"{frontend_base}/auth/reset-password?uid={uid}&token={token}"

        subject = "Reset your password"
        body = (
            "You requested to reset your password.\n\n"
            f"Click the link below to set a new password (valid once):\n{reset_url}\n\n"
            "If you did not request this, you can ignore this email."
        )
        try:
            # Dispatch email via Celery to avoid blocking the request
            from .tasks import send_email_task
            send_email_task.delay(subject, body, email)
        except Exception:
            # Avoid leaking SMTP/celery details to client
            pass

        return {"message": "If an account exists, you will receive an email shortly."}
    except Exception as e:
        # Generic response to avoid enumeration and leaking details
        return {"message": "If an account exists, you will receive an email shortly."}


@api.post("/auth/reset-password")
def reset_password(request, payload: ResetPasswordIn):
    """Complete password reset using uid/token and set a new password."""
    try:
        uid = payload.uid
        token = payload.token
        new_password = payload.new_password
        if not uid or not token or not new_password:
            return api.create_response(request, {"detail": "Missing fields"}, status=400)

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id, is_active=True)
        except Exception:
            return api.create_response(request, {"detail": "Invalid link"}, status=400)

        if not PasswordResetTokenGenerator().check_token(user, token):
            return api.create_response(request, {"detail": "Invalid or expired token"}, status=400)

        try:
            validate_password(new_password, user=user)
        except ValidationError as ve:
            return api.create_response(request, {"detail": ve.messages}, status=400)

        user.set_password(new_password)
        user.save()
        return {"success": True, "message": "Password has been reset successfully."}
    except Exception as e:
        return api.create_response(request, {"detail": "Could not reset password"}, status=500)


# ========================
# Auth: OTP-based Forgot/Reset Password
# ========================

class SendOtpIn(Schema):
    email: str


class ResetWithOtpIn(Schema):
    email: str
    otp: str
    new_password: str


def _otp_cache_key(email: str) -> str:
    return f"pwd_otp:{email.lower()}"


def _otp_cooldown_key(email: str) -> str:
    return f"pwd_otp_cd:{email.lower()}"


@api.post("/auth/forgot-password/otp/send")
def send_reset_otp(request, payload: SendOtpIn):
    """Send a 6-digit OTP to the user's email. Applies 60s resend cooldown and 10-minute expiry.
    Always returns a generic success-like message to avoid email enumeration.
    """
    try:
        email = (payload.email or "").strip().lower()
        if not email:
            return api.create_response(request, {"detail": "Email is required"}, status=400)

        # Enforce resend cooldown (60 seconds)
        if cache.get(_otp_cooldown_key(email)):
            return {"message": "If an account exists, you will receive an OTP shortly."}

        # Check if user exists (but don't disclose outcome)
        user_exists = User.objects.filter(email=email, is_active=True).exists()

        if user_exists:
            # Generate cryptographically strong 6-digit numeric OTP
            otp = "".join(secrets.choice("0123456789") for _ in range(6))
            # Store OTP with expiry (10 minutes), track attempts
            cache.set(_otp_cache_key(email), {"otp": otp, "attempts": 0, "created_at": timezone.now().isoformat()}, timeout=10 * 60)

            subject = "Your password reset OTP"
            body = (
                "Use the OTP below to reset your password.\n\n"
                f"OTP: {otp}\n"
                "This code is valid for 10 minutes. Do not share it with anyone."
            )
            try:
                # Dispatch email via Celery to avoid blocking the request
                from .tasks import send_email_task
                send_email_task.delay(subject, body, email)
            except Exception:
                # Do not leak mail server/celery errors to client
                pass

        # Set resend cooldown regardless of existence
        cache.set(_otp_cooldown_key(email), True, timeout=60)
        return {"message": "If an account exists, you will receive an OTP shortly."}
    except Exception:
        # Generic response to avoid leaking details
        return {"message": "If an account exists, you will receive an OTP shortly."}


@api.post("/auth/forgot-password/otp/reset")
def reset_password_with_otp(request, payload: ResetWithOtpIn):
    """Reset password using email + OTP. Validates OTP (10-minute TTL), limits attempts, and applies Django password validators."""
    try:
        email = (payload.email or "").strip().lower()
        otp = (payload.otp or "").strip()
        new_password = payload.new_password

        if not email or not otp or not new_password:
            return api.create_response(request, {"detail": "Missing fields"}, status=400)

        try:
            user = User.objects.get(email=email, is_active=True)
        except User.DoesNotExist:
            # Do not reveal non-existence; pretend it's an OTP failure
            return api.create_response(request, {"detail": "Invalid or expired OTP"}, status=400)

        entry = cache.get(_otp_cache_key(email))
        if not entry or not isinstance(entry, dict):
            return api.create_response(request, {"detail": "Invalid or expired OTP"}, status=400)

        attempts = int(entry.get("attempts", 0))
        if attempts >= 5:
            # Too many attempts; invalidate and ask to re-request
            cache.delete(_otp_cache_key(email))
            return api.create_response(request, {"detail": "OTP attempts exceeded. Please request a new OTP."}, status=400)

        if entry.get("otp") != otp:
            # Increment attempt count and keep entry until TTL
            entry["attempts"] = attempts + 1
            cache.set(_otp_cache_key(email), entry, timeout=10 * 60)
            return api.create_response(request, {"detail": "Invalid or expired OTP"}, status=400)

        # Validate and set new password
        try:
            validate_password(new_password, user=user)
        except ValidationError as ve:
            return api.create_response(request, {"detail": ve.messages}, status=400)

        user.set_password(new_password)
        user.save(update_fields=["password"]) 

        # Invalidate OTP after successful reset
        cache.delete(_otp_cache_key(email))
        cache.delete(_otp_cooldown_key(email))
        return {"success": True, "message": "Password has been reset successfully."}
    except Exception:
        return api.create_response(request, {"detail": "Could not reset password"}, status=500)


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

    async_to_sync(notify_user_update)("created", user)
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
    async_to_sync(notify_user_update)("updated", user)
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
                "branch": profile.branch or "",
                "year": profile.year or "",
                "lab_days": [profile.lab_day] if profile.lab_day else [],  # ✅ Always list
            })
        except StudentProfile.DoesNotExist:
            user_data.update({
                "roll_number": "",
                "branch": "",
                "year": "",
                "lab_day": "",
            })

    elif user.role == "faculty":
        try:
            profile = FacultyProfile.objects.get(user=user)
            user_data.update({
                "department": profile.department or "",
                "lab_days": profile.lab_days or [],  # multiple
            })
        except FacultyProfile.DoesNotExist:
            user_data.update({
                "department": "",
                "lab_days": [],
            })

    elif user.role == "staff":
        try:
            profile = StaffProfile.objects.get(user=user)
            user_data.update({
                "department": profile.department or "",
                "lab_days": profile.lab_days or [],  # multiple
            })
        except StaffProfile.DoesNotExist:
            user_data.update({
                "department": "",
                "lab_days": [],
            })

    response = {"authenticated": True, "user": user_data}
    cache.set(cache_key, response, timeout=60 * 5)  # Cache for 5 minutes
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

            # TEMP DISABLED: Domain restriction during import to allow broader emails for testing
            # if not email.lower().endswith("@lnmiit.ac.in"):
            #     raise ValueError("Only LNMIIT emails are allowed")

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

    # Return schema-compatible response
    return UploadedFileOutSchema(
        id=uploaded.id,
        user=uploaded.user.id,
        filename=uploaded.filename,
        size=uploaded.size,
        cdn_url=uploaded.cdn_url,
        year=uploaded.year,
        uploaded_at=uploaded.uploaded_at,  # ✅ include this
    )




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
    async_to_sync(notify_file_update)("created", uploaded)

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

    if request.user.role in ["admin", "faculty"]:
        files = UploadedFileModel.objects.all()
    elif request.user.role == "student":
        # Student: show files for their year or "All"
        year = None
        try:
            profile = StudentProfile.objects.get(user=request.user)
            year = 'Y' + profile.roll_number[:2]
        except StudentProfile.DoesNotExist:
            year = None
        files = UploadedFileModel.objects.filter(
            year__in=["All", year]
        )
    else:
        # Staff or others: only their own uploads
        files = UploadedFileModel.objects.filter(user=request.user)

    files = files.order_by("-uploaded_at")

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
    # cache.set(cache_key, result, timeout=300)
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
    async_to_sync(notify_file_update)("deleted", uploaded_file)

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
    
#Triggering event
from channels.layers import get_channel_layer


async def notify_file_update(event_type, file_obj):
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "file_updates",
        {
            "type": "send_file_update",
            "data": {
                "event": event_type,
                "id": file_obj.id,
                "filename": file_obj.filename,
                "size": file_obj.size,
                "year": file_obj.year,
                "cdn_url": file_obj.cdn_url,
                "uploaded_at": str(file_obj.uploaded_at),
            },
        },
    )

async def notify_user_update(event_type, user_obj):
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "user_updates",
        {
            "type": "send_user_update",
            "data": {
                "event": event_type,
                "id": user_obj.id,
                "username": user_obj.username,
                "email": user_obj.email,
                "role": user_obj.role,
            },
        },
    )


async def notify_bill_update(event_type, bill_obj):
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "bill_updates",
        {
            "type": "send_bill_update",
            "data": {
                "event": event_type,
                "id": bill_obj.id,
                "bill_no": bill_obj.bill_no,
                "amount": float(bill_obj.amount),
                "file_url": bill_obj.file_url,
                "original_filename": bill_obj.original_filename,
                "public_id": bill_obj.public_id,
                "resource_type": bill_obj.resource_type,
                "comment": getattr(bill_obj, "comment", None),
                "uploaded_at": str(bill_obj.uploaded_at),
                "financial_year": bill_obj.financial_year,
            },
        },
    )


@api.get("/users", response=List[UserOut])
def list_users(request, role: Optional[str] = None, sort_by: Optional[str] = None, year: Optional[str] = None):
    """
    Returns all users with full profile details depending on role:
    - Students: branch, year, lab_day, roll_number
    - Faculty: department, lab_days
    - Staff: department, lab_days
    """
    users = CustomUser.objects.all()

    # Filter by role
    if role:
        users = users.filter(role=role)

    # Filter by student year
    if role == "student" and year:
        users = users.filter(student_profile__year=year)

    # Sorting
    if sort_by == "name":
        users = users.order_by("username")
    elif sort_by == "login":
        users = users.order_by("-last_login")

    user_list = []

    for u in users:
        branch = year_val = None
        lab_days = []
        department = None
        roll_number = None

        if u.role == "student":
            try:
                sp = u.student_profile
                branch = sp.branch
                year_val = sp.year
                roll_number = sp.roll_number
                lab_days = [sp.lab_day] if sp.lab_day else []
            except StudentProfile.DoesNotExist:
                pass

        elif u.role == "faculty":
            try:
                fp = u.faculty_profile
                department = fp.department
                lab_days = fp.lab_days or []
            except FacultyProfile.DoesNotExist:
                pass

        elif u.role == "staff":
            try:
                sf = u.staff_profile
                department = sf.department
                lab_days = sf.lab_days or []
            except StaffProfile.DoesNotExist:
                pass

        user_list.append(
            UserOut(
                id=u.id,
                email=u.email,
                username=u.username,
                role=u.role,
                phone=u.phone,
                branch=branch,
                year=year_val,
                lab_days=lab_days,
                department=department,
                last_login=u.last_login.isoformat() if u.last_login else None,
                date_joined=u.date_joined.isoformat() if u.date_joined else None,
                is_active=u.is_active,
            )
        )

    return user_list


# ========================
# 🧑‍🎓 Create Student
# ========================
@api.post("/create-student")
def create_student(request, email: str = Form(...), username: str = Form(...), password: str = Form(...),
                   roll_number: str = Form(...), branch: str = Form(...), year: str = Form(...), lab_day: str = Form(...)):
    if CustomUser.objects.filter(email=email).exists():
        return {"error": "User with this email already exists"}

    user = CustomUser.objects.create_user(
        email=email,
        username=username,
        password=password,
        role="student",
        branch=branch,
        year=year,
        lab_days=[lab_day]
    )

    StudentProfile.objects.create(
        user=user,
        roll_number=roll_number,
        branch=branch,
        year=year,
        lab_day=lab_day
    )
    return {"message": "Student created successfully", "id": user.id}


# ========================
# 🧑‍🏫 Create Faculty
# ========================
@api.post("/create-faculty")
def create_faculty(request, email: str = Form(...), username: str = Form(...), password: str = Form(...),
                   department: str = Form(...), lab_days: List[str] = Form(...)):
    if CustomUser.objects.filter(email=email).exists():
        return {"error": "User with this email already exists"}

    user = CustomUser.objects.create_user(
        email=email,
        username=username,
        password=password,
        role="faculty",
        lab_days=lab_days
    )

    FacultyProfile.objects.create(user=user, department=department, lab_days=lab_days)
    return {"message": "Faculty created successfully", "id": user.id}


# ========================
# 🧑‍🔧 Create Staff
# ========================
@api.post("/create-staff")
def create_staff(request, email: str = Form(...), username: str = Form(...), password: str = Form(...),
                 department: str = Form(...), lab_days: List[str] = Form(...)):
    if CustomUser.objects.filter(email=email).exists():
        return {"error": "User with this email already exists"}

    user = CustomUser.objects.create_user(
        email=email,
        username=username,
        password=password,
        role="staff",
        lab_days=lab_days
    )

    StaffProfile.objects.create(user=user, department=department, lab_days=lab_days)
    return {"message": "Staff created successfully", "id": user.id}

from ninja import File
from ninja.files import UploadedFile as NinjaUploadFile
import cloudinary
import cloudinary.uploader
from django.conf import settings
from .models import Bill
from .schemas import BillOut, PaginatedBills
from ninja import Schema

class UpdateBillCommentIn(Schema):
    comment: Optional[str] = None

@api.post("/bills/upload")
def upload_bill(request, bill_no: str = Form(...), amount: float = Form(...), file: NinjaUploadFile = File(...), comment: Optional[str] = Form(None)):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)
    try:
        # Prefer underlying Django file object for uploads
        django_file = getattr(file, "file", None) or file
        original_name = getattr(file, "name", "bill")

        # Extract extension and content type
        file_ext = ""
        if "." in original_name:
            file_ext = original_name.rsplit(".", 1)[-1].lower()
        content_type = getattr(file, "content_type", None) or ""

        # Classify type
        image_exts = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff"}
        video_exts = {"mp4", "webm", "mov", "avi", "mkv", "m4v", "ogv"}
        is_image = content_type.startswith("image/") or file_ext in image_exts
        is_video = content_type.startswith("video/") or file_ext in video_exts
        is_pdf = (content_type == "application/pdf") or file_ext == "pdf"

        # Ensure Cloudinary is configured (defensive)
        cfg = cloudinary.config()
        if not (cfg.api_key and cfg.api_secret and cfg.cloud_name):
            cloudinary.config(
                cloud_name=getattr(settings, 'CLOUDINARY_CLOUD_NAME', None) or os.getenv('CLOUDINARY_CLOUD_NAME'),
                api_key=getattr(settings, 'CLOUDINARY_API_KEY', None) or os.getenv('CLOUDINARY_API_KEY'),
                api_secret=getattr(settings, 'CLOUDINARY_API_SECRET', None) or os.getenv('CLOUDINARY_API_SECRET'),
                secure=True,
            )
            cfg = cloudinary.config()
            if not (cfg.api_key and cfg.api_secret and cfg.cloud_name):
                return api.create_response(
                    request,
                    {"detail": "Server missing Cloudinary credentials. Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET or CLOUDINARY_URL."},
                    status=500,
                )

        # Prepare Cloudinary upload for ALL files (images, videos, PDFs, docs)
        unique_id = uuid.uuid4().hex[:12]
        clean_name = original_name.rsplit(".", 1)[0] if "." in original_name else original_name
        clean_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in clean_name)[:50]
        public_id_base = f"bills/{clean_name}_{unique_id}"

        # Decide resource_type to make files accessible and previewable
        # - video for videos
        # - image for images and PDFs (PDFs are well-supported under image type)
        # - raw for other documents (docx, xlsx, etc.)
        if is_video:
            chosen_resource_type = "video"
        elif is_image or is_pdf:
            chosen_resource_type = "image"
        else:
            chosen_resource_type = "raw"

        upload_params = {
            "public_id": public_id_base,
            "resource_type": chosen_resource_type,
            "overwrite": False,
            "type": "upload",  # public & previewable
        }
        if file_ext:
            upload_params["format"] = file_ext

        result = cloudinary.uploader.upload(django_file, **upload_params)
        print(f"[Cloudinary Upload] File: {original_name}")
        print(f"[Cloudinary Upload] Public ID: {result.get('public_id')}")
        print(f"[Cloudinary Upload] Resource Type: {result.get('resource_type')}")
        print(f"[Cloudinary Upload] Format: {result.get('format')}")
        print(f"[Cloudinary Upload] URL: {result.get('secure_url')}")

        public_id = result.get("public_id")
        file_url = result.get("secure_url") or result.get("url")
        if not file_url:
            return api.create_response(request, {"detail": "Cloudinary returned no URL"}, status=502)

        bill = Bill.objects.create(
            bill_no=bill_no,
            amount=amount,
            file_url=file_url,
            original_filename=original_name,
            public_id=public_id,
            resource_type=result.get("resource_type"),
            comment=comment or None,
            uploaded_by=request.user,
        )

        # NOTE: Previous behavior stored PDFs/docs in Supabase. Requested change: everything goes to Cloudinary.
        # The old Supabase branch is kept here commented out for reference.
        #
        # else:
        #     # PDFs and other documents -> Supabase storage (OLD PATH)
        #     supabase_path = upload_to_supabase(file, original_name)
        #     print(f"[Supabase Upload] File: {original_name} -> Path: {supabase_path}")
        #     bill = Bill.objects.create(
        #         bill_no=bill_no,
        #         amount=amount,
        #         file_url=supabase_path,  # store storage path, we'll sign on access
        #         original_filename=original_name,
        #         public_id=None,
        #         resource_type="supabase",
        #         uploaded_by=request.user,
        #     )

        # Notify via WebSocket
        try:
            async_to_sync(notify_bill_update)("created", bill)
        except Exception as _:
            pass

        # Invalidate list/years caches
        _bills_bump_version()

        return {
            "id": bill.id,
            "bill_no": bill.bill_no,
            "amount": float(bill.amount),
            "file_url": bill.file_url,
            "original_filename": bill.original_filename,
            "public_id": bill.public_id,
            "resource_type": bill.resource_type,
            "comment": bill.comment,
            "uploaded_at": bill.uploaded_at,
            "financial_year": bill.financial_year,
        }
    except Exception as e:
        return api.create_response(request, {"detail": f"Upload failed: {e}"}, status=500)

@api.get("/bills", response=PaginatedBills)
def list_bills(request, fy: Optional[str] = None, page: int = 1, limit: int = 10):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)

    page = max(1, page)
    limit = max(1, min(100, limit))

    # Try cached page first
    cache_key = _bills_cache_key(fy, page, limit)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Thin query: fetch only needed fields
    qs = Bill.objects.only(
        "id",
        "bill_no",
        "amount",
        "file_url",
        "original_filename",
        "public_id",
        "resource_type",
        "comment",
        "uploaded_at",
        "financial_year",
    ).order_by("-uploaded_at")
    if fy:
        qs = qs.filter(financial_year=fy)
    total = qs.count()
    start = (page - 1) * limit
    items_qs = qs[start:start + limit]
    total_pages = (total + limit - 1) // limit

    items = [
        BillOut(
            id=b.id,
            bill_no=b.bill_no,
            amount=float(b.amount),
            file_url=b.file_url,
            original_filename=b.original_filename,
            public_id=b.public_id,
            resource_type=b.resource_type,
            comment=getattr(b, "comment", None),
            uploaded_at=b.uploaded_at,
            financial_year=b.financial_year,
        )
        for b in items_qs
    ]
    result = {
        "items": items,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    }
    # Longer TTL since we version-invalidate on mutations; safe to cache for a day
    cache.set(cache_key, result, timeout=86400)
    return result

@api.put("/bills/{bill_id}/comment")
def update_bill_comment(request, bill_id: int, payload: UpdateBillCommentIn):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return api.create_response(request, {"detail": "Not found"}, status=404)

    bill.comment = (payload.comment or "").strip() or None
    bill.save(update_fields=["comment", "uploaded_at"])  # uploaded_at unchanged but harmless

    # Notify via WebSocket and invalidate caches
    try:
        async_to_sync(notify_bill_update)("updated", bill)
    except Exception:
        pass
    _bills_bump_version()

    return {
        "id": bill.id,
        "bill_no": bill.bill_no,
        "amount": float(bill.amount),
        "file_url": bill.file_url,
        "original_filename": bill.original_filename,
        "public_id": bill.public_id,
        "resource_type": bill.resource_type,
        "comment": bill.comment,
        "uploaded_at": bill.uploaded_at,
        "financial_year": bill.financial_year,
    }

@api.get("/bills/years")
def list_bill_years(request):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)
    years_cache_key = _bills_years_cache_key()
    years_cached = cache.get(years_cache_key)
    if years_cached is not None:
        return years_cached

    years = (
        Bill.objects.values_list("financial_year", flat=True)
        .distinct()
        .order_by("-financial_year")
    )
    years_list = list(years)
    # Cache years for a day; version bump invalidates
    cache.set(years_cache_key, years_list, timeout=86400)
    return years_list

@api.delete("/bills/{bill_id}")
def delete_bill(request, bill_id: int):
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return api.create_response(request, {"detail": "Not found"}, status=404)

    # Try to remove asset from its storage
    # Keep a snapshot for websocket notification before delete
    bill_data = bill
    try:
        if (bill.resource_type == "supabase") or (bill.file_url and not str(bill.file_url).startswith(("http://", "https://"))):
            # Supabase removal expects the storage path
            try:
                res = supabase.storage.from_(SUPABASE_BUCKET).remove([bill.file_url])
                if hasattr(res, "error") and res.error:
                    print(f"[Supabase] delete error: {res.error.message}")
            except Exception as se:
                print(f"[Supabase] delete failed for {bill.file_url}: {se}")
        elif bill.public_id:
            cloudinary.uploader.destroy(
                bill.public_id,
                resource_type=bill.resource_type or "image",
                invalidate=True,
            )
    except Exception as e:
        # Log and continue deleting DB row
        print(f"[Delete Bill] storage cleanup failed for {bill.id}: {e}")

    bill.delete()
    # Notify via WebSocket
    try:
        async_to_sync(notify_bill_update)("deleted", bill_data)
    except Exception as _:
        pass
    # Invalidate list/years caches
    _bills_bump_version()
    return {"success": True}


@api.get("/bills/{bill_id}/view")
def view_bill_file(request, bill_id: int):
    """Proxy endpoint to serve bill files with correct Content-Type headers for inline viewing."""
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)
    
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return api.create_response(request, {"detail": "Bill not found"}, status=404)
    
    print(f"[View Bill] Fetching file for bill {bill_id}, URL: {bill.file_url}")
    
    # If stored in Supabase, generate a signed URL and redirect the client directly
    try:
        if (bill.resource_type == "supabase") or (bill.file_url and not str(bill.file_url).startswith(("http://", "https://"))):
            path = bill.file_url
            # Short-lived signed URL; let browser fetch directly from Supabase CDN for lowest latency
            try:
                signed_url = get_signed_url(path, expires_in=300)
            except Exception as e:
                return api.create_response(request, {"detail": f"Failed to create signed URL: {e}"}, status=500)

            from django.http import HttpResponseRedirect
            resp = HttpResponseRedirect(signed_url)
            # Hint caches a little; actual content caching handled by Supabase CDN
            resp["Cache-Control"] = "private, max-age=60"
            return resp
    except Exception as e:
        print(f"[View Bill Supabase] Error: {type(e).__name__}: {e}")
        # Fall through to Cloudinary branch if something unexpected happens
    
    # Build a signed Cloudinary URL to avoid 401s on restricted deliveries
    try:
        import re
        import requests
        from cloudinary.utils import cloudinary_url, private_download_url
        import time

        # Derive extension and version if available
        ext = ""
        if bill.original_filename and "." in bill.original_filename:
            ext = bill.original_filename.rsplit(".", 1)[-1].lower()

        version = None
        # Try to parse version like "/v1761848120/" from stored URL
        if bill.file_url:
            m = re.search(r"/v(\d+)/", bill.file_url)
            if m:
                try:
                    version = int(m.group(1))
                except Exception:
                    version = None

        # Prefer stored resource_type; default to image (Cloudinary stores PDFs under image)
        rt = bill.resource_type or "image"

        # Normalize public_id: strip extension if mistakenly stored with one
        pub_id = bill.public_id or ""
        try:
            last = pub_id.rsplit("/", 1)[-1]
            if "." in last:
                pub_id_base = pub_id[: -(len(last))] + last.rsplit(".", 1)[0]
            else:
                pub_id_base = pub_id
        except Exception:
            pub_id_base = pub_id
        if not ext and bill.file_url and "." in bill.file_url:
            try:
                ext = bill.file_url.rsplit(".", 1)[-1].lower().split("?")[0]
            except Exception:
                pass

        # Normalize public_id: strip extension if mistakenly stored with one
        pub_id = bill.public_id or ""
        try:
            last = pub_id.rsplit("/", 1)[-1]
            if "." in last:
                pub_id_base = pub_id[: -(len(last))] + last.rsplit(".", 1)[0]
            else:
                pub_id_base = pub_id
        except Exception:
            pub_id_base = pub_id
        if not ext and bill.file_url and "." in bill.file_url:
            try:
                ext = bill.file_url.rsplit(".", 1)[-1].lower().split("?")[0]
            except Exception:
                pass
        print(f"[Download Bill] Using public_id base: {pub_id_base}, ext: {ext}, rt: {rt}, v: {version}")

        # Normalize public_id: strip extension if mistakenly stored with one
        pub_id = bill.public_id or ""
        try:
            last = pub_id.rsplit("/", 1)[-1]
            if "." in last:
                pub_id_base = pub_id[: -(len(last))] + last.rsplit(".", 1)[0]
            else:
                pub_id_base = pub_id
        except Exception:
            pub_id_base = pub_id
        if not ext and bill.file_url and "." in bill.file_url:
            try:
                ext = bill.file_url.rsplit(".", 1)[-1].lower().split("?")[0]
            except Exception:
                pass
        print(f"[View Bill] Using public_id base: {pub_id_base}, ext: {ext}, rt: {rt}, v: {version}")

        # Generate a signed URL (works with strict/signed delivery accounts)
        signed_url, _ = cloudinary_url(
            pub_id_base,
            format=ext or None,
            resource_type=rt,
            type="upload",
            version=version,
            sign_url=True,
            secure=True,
        )

        fetch_url = signed_url or bill.file_url
        print(f"[View Bill] Fetch URL: {fetch_url}")

        # Support Range requests for better PDF viewing
        range_header = request.META.get("HTTP_RANGE")
        req_headers = {}
        if range_header:
            req_headers["Range"] = range_header
            print(f"[View Bill] Forwarding Range: {range_header}")

        response = requests.get(fetch_url, timeout=30, allow_redirects=True, headers=req_headers, stream=True)
        print(f"[View Bill] Cloudinary response status: {response.status_code}, Content-Type: {response.headers.get('Content-Type')}")

        if response.status_code not in (200, 206):
            print(f"[View Bill] Primary fetch failed ({response.status_code}). Trying direct URL then private_download_url fallback...")
            # Try direct stored URL (public upload URL)
            try:
                direct_resp = requests.get(bill.file_url, timeout=30, allow_redirects=True, headers=req_headers, stream=True)
                if direct_resp.status_code in (200, 206):
                    response = direct_resp
                    print("[View Bill] Direct URL succeeded")
                else:
                    print(f"[View Bill] Direct URL failed ({direct_resp.status_code})")
            except Exception as pe:
                print(f"[View Bill] Direct URL error: {pe}")

        if response.status_code not in (200, 206):
            try:
                expires_at = int(time.time()) + 120
                purl = private_download_url(
                    pub_id_base,
                    ext or None,
                    resource_type=rt,
                    type="upload",
                    expires_at=expires_at,
                )
                print(f"[View Bill] Fallback URL: {purl}")
                response = requests.get(purl, timeout=30, allow_redirects=True, headers=req_headers, stream=True)
                print(f"[View Bill] Fallback status: {response.status_code}")
            except Exception as pe:
                print(f"[View Bill] Fallback generation error: {pe}")

        if response.status_code not in (200, 206):
            # Fallback 2: try private_download_url again (explicit type)
            try:
                purl2 = private_download_url(
                    pub_id_base,
                    ext or None,
                    resource_type=rt,
                    type="upload",
                )
                print(f"[View Bill] Second fallback URL: {purl2}")
                response = requests.get(purl2, timeout=30, allow_redirects=True, headers=req_headers, stream=True)
            except Exception as pe:
                print(f"[View Bill] Second fallback generation error: {pe}")

        # Note: We avoid switching resource_type (e.g., to raw) if the asset lives under image.

        if response.status_code not in (200, 206):
            snippet = ''
            try:
                snippet = response.text[:200]
            except Exception:
                pass
            print(f"[View Bill] Failed to fetch. Status: {response.status_code}, Response: {snippet}")
            return api.create_response(request, {"detail": f"Failed to fetch file from Cloudinary (status {response.status_code})"}, status=502)

        # Determine content type (prefer upstream header)
        content_type = response.headers.get("Content-Type") or "application/octet-stream"
        if content_type == "application/octet-stream" and ext:
            if ext == "pdf":
                content_type = "application/pdf"
            elif ext in ["jpg", "jpeg"]:
                content_type = "image/jpeg"
            elif ext == "png":
                content_type = "image/png"
            elif ext == "gif":
                content_type = "image/gif"

        from django.http import StreamingHttpResponse
        def stream():
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        status_code = 206 if response.status_code == 206 else 200
        http_response = StreamingHttpResponse(stream(), content_type=content_type, status=status_code)
        http_response["Content-Disposition"] = f'inline; filename="{bill.original_filename or "file"}"'
        http_response["Cache-Control"] = "public, max-age=3600"
        http_response["Accept-Ranges"] = "bytes"
        if response.headers.get("Content-Range"):
            http_response["Content-Range"] = response.headers["Content-Range"]
        if response.headers.get("Content-Length"):
            http_response["Content-Length"] = response.headers["Content-Length"]
        return http_response
        
    except Exception as e:
        print(f"[View Bill] Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return api.create_response(request, {"detail": f"Failed to load file: {str(e)}"}, status=500)


@api.get("/bills/{bill_id}/download")
def download_bill_file(request, bill_id: int):
    """Proxy endpoint to download bill files with correct filename."""
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)
    
    try:
        bill = Bill.objects.get(id=bill_id)
    except Bill.DoesNotExist:
        return api.create_response(request, {"detail": "Bill not found"}, status=404)
    
    print(f"[Download Bill] Fetching file for bill {bill_id}, URL: {bill.file_url}")
    
    # Supabase-backed file: sign and stream download
    try:
        if (bill.resource_type == "supabase") or (bill.file_url and not str(bill.file_url).startswith(("http://", "https://"))):
            path = bill.file_url
            try:
                signed_url = get_signed_url(path, expires_in=300)
            except Exception as e:
                return api.create_response(request, {"detail": f"Failed to create signed URL: {e}"}, status=500)

            import requests as _requests
            resp = _requests.get(signed_url, stream=True, timeout=30)
            if resp.status_code != 200:
                return api.create_response(request, {"detail": f"Failed to fetch file (status {resp.status_code})"}, status=502)

            ext = ""
            if bill.original_filename and "." in bill.original_filename:
                ext = bill.original_filename.rsplit(".", 1)[-1].lower()
            content_type = resp.headers.get("Content-Type") or "application/octet-stream"
            if content_type == "application/octet-stream" and ext:
                if ext == "pdf":
                    content_type = "application/pdf"
                elif ext in ["jpg", "jpeg"]:
                    content_type = "image/jpeg"
                elif ext == "png":
                    content_type = "image/png"
                elif ext == "gif":
                    content_type = "image/gif"

            from django.http import StreamingHttpResponse
            def _stream():
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            http_response = StreamingHttpResponse(_stream(), content_type=content_type)
            http_response["Content-Disposition"] = f'attachment; filename="{bill.original_filename or "file"}"'
            return http_response
    except Exception as e:
        print(f"[Download Bill Supabase] Error: {type(e).__name__}: {e}")
        # Fall through to Cloudinary branch if unexpected
    
    # Build a signed Cloudinary URL to avoid 401s on restricted deliveries
    try:
        import re
        import requests
        from cloudinary.utils import cloudinary_url, private_download_url
        import time

        # Derive extension and version if available
        ext = ""
        if bill.original_filename and "." in bill.original_filename:
            ext = bill.original_filename.rsplit(".", 1)[-1].lower()

        version = None
        if bill.file_url:
            m = re.search(r"/v(\d+)/", bill.file_url)
            if m:
                try:
                    version = int(m.group(1))
                except Exception:
                    version = None

        rt = bill.resource_type or "image"

        # Normalize public_id for download flow
        pub_id = bill.public_id or ""
        try:
            last = pub_id.rsplit("/", 1)[-1]
            if "." in last:
                pub_id_base = pub_id[: -(len(last))] + last.rsplit(".", 1)[0]
            else:
                pub_id_base = pub_id
        except Exception:
            pub_id_base = pub_id
        if not ext and bill.file_url and "." in bill.file_url:
            try:
                ext = bill.file_url.rsplit(".", 1)[-1].lower().split("?")[0]
            except Exception:
                pass
        print(f"[Download Bill] Using public_id base: {pub_id_base}, ext: {ext}, rt: {rt}, v: {version}")

        signed_url, _ = cloudinary_url(
            pub_id_base,
            format=ext or None,
            resource_type=rt,
            type="upload",
            version=version,
            sign_url=True,
            secure=True,
        )

        fetch_url = signed_url or bill.file_url
        print(f"[Download Bill] Fetch URL: {fetch_url}")

        response = requests.get(fetch_url, timeout=30, allow_redirects=True, stream=True)
        print(f"[Download Bill] Cloudinary response status: {response.status_code}")

        if response.status_code != 200:
            print(f"[Download Bill] Primary fetch failed ({response.status_code}). Trying direct URL then private_download_url fallback...")
            # Try direct stored URL (public upload URL)
            try:
                direct_resp = requests.get(bill.file_url, timeout=30, allow_redirects=True, stream=True)
                if direct_resp.status_code == 200:
                    response = direct_resp
                    print("[Download Bill] Direct URL succeeded")
                else:
                    print(f"[Download Bill] Direct URL failed ({direct_resp.status_code})")
            except Exception as pe:
                print(f"[Download Bill] Direct URL error: {pe}")

            if response.status_code != 200:
                try:
                    expires_at = int(time.time()) + 120
                    purl = private_download_url(
                        pub_id_base,
                        ext or None,
                        resource_type=rt,
                        type="upload",
                        expires_at=expires_at,
                    )
                    print(f"[Download Bill] Fallback URL: {purl}")
                    response = requests.get(purl, timeout=30, allow_redirects=True, stream=True)
                    print(f"[Download Bill] Fallback status: {response.status_code}")
                except Exception as pe:
                    print(f"[Download Bill] Fallback generation error: {pe}")

        # Note: avoid switching resource_type when the asset is under image.

        if response.status_code != 200:
            print(f"[Download Bill] Failed to fetch. Status: {response.status_code}")
            return api.create_response(request, {"detail": f"Failed to fetch file from Cloudinary (status {response.status_code})"}, status=502)
        
        # Determine content type from filename
        content_type = "application/octet-stream"
        if ext == "pdf":
            content_type = "application/pdf"
        elif ext in ["jpg", "jpeg"]:
            content_type = "image/jpeg"
        elif ext == "png":
            content_type = "image/png"
        elif ext == "gif":
            content_type = "image/gif"
        
        print(f"[Download Bill] Serving file for download, size: {len(response.content)} bytes")
        
        # Return file with attachment disposition to force download
        from django.http import StreamingHttpResponse
        def stream():
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        http_response = StreamingHttpResponse(stream(), content_type=content_type)
        http_response["Content-Disposition"] = f'attachment; filename="{bill.original_filename or "file"}"'
        return http_response
        
    except Exception as e:
        print(f"[Download Bill] Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return api.create_response(request, {"detail": f"Failed to load file: {str(e)}"}, status=500)


# Removed public-token/public endpoints for Google Viewer to simplify codebase and avoid unused routes

# =============================
# Bills: Server-side Excel export
# =============================
@api.get("/bills/export.xlsx")
def export_bills_xlsx(request, fy: Optional[str] = None, q: Optional[str] = None):
    """Stream an Excel workbook for the current bills filter.

    Query params:
    - fy: financial year filter (optional)
    - q: search text applied to bill_no, original_filename, or comment (optional)
    """
    if not request.user.is_authenticated:
        return api.create_response(request, {"detail": "Authentication required"}, status=401)
    if getattr(request.user, "role", None) != "admin":
        return api.create_response(request, {"detail": "Unauthorized"}, status=403)

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.cell.cell import WriteOnlyCell
    from django.db.models import Q
    from django.utils.timezone import localtime

    # Build queryset with optional filters
    qs = Bill.objects.all().only(
        "id",
        "bill_no",
        "amount",
        "file_url",
        "original_filename",
        "public_id",
        "resource_type",
        "comment",
        "uploaded_at",
        "financial_year",
    ).order_by("-uploaded_at")
    if fy:
        qs = qs.filter(financial_year=fy)
    if q:
        q = q.strip()
        if q:
            qs = qs.filter(
                Q(bill_no__icontains=q)
                | Q(original_filename__icontains=q)
                | Q(comment__icontains=q)
            )

    wb = Workbook()
    ws = wb.create_sheet(title="Bills")
    # Remove default sheet if present
    if wb.worksheets and wb.worksheets[0].title == "Sheet":
        wb.remove(wb.worksheets[0])

    # Removed FY column (downloading for a specific/current year only)
    headers = ["Name", "Bill No", "Amount", "Upload Date", "Comment", "View", "Download"]
    header_font = Font(bold=True)
    header_cells = [WriteOnlyCell(ws, value=h) for h in headers]
    for c in header_cells:
        c.font = header_font
    ws.append(header_cells)

    base = request.build_absolute_uri("/").rstrip("/")

    # Stream rows
    for b in qs.iterator(chunk_size=2000):
        name = b.original_filename or b.bill_no or "bill"
        bill_no = b.bill_no or ""
        amount = float(b.amount or 0)
        # Show date only (no time)
        uploaded = localtime(b.uploaded_at).strftime("%Y-%m-%d")
        comment = (b.comment or "").replace("\r", " ").replace("\n", " ")
        view_url = f"{base}/api/bills/{b.id}/view"
        dl_url = f"{base}/api/bills/{b.id}/download"

        name_cell = WriteOnlyCell(ws, value=name)
        name_cell.hyperlink = view_url
        name_cell.style = "Hyperlink"

        view_cell = WriteOnlyCell(ws, value="View")
        view_cell.hyperlink = view_url
        view_cell.style = "Hyperlink"

        dl_cell = WriteOnlyCell(ws, value="Download")
        dl_cell.hyperlink = dl_url
        dl_cell.style = "Hyperlink"

        # Optional: ensure long comments don't visually collide by allowing wrap (Excel may auto size row)
        date_cell = WriteOnlyCell(ws, value=uploaded)
        date_cell.alignment = Alignment(wrap_text=True)

        ws.append([
            name_cell,
            bill_no,
            amount,
            date_cell,
            comment,
            view_cell,
            dl_cell,
        ])

    # Set basic column widths
    ws.column_dimensions['A'].width = 40  # Name
    ws.column_dimensions['B'].width = 14  # Bill No
    ws.column_dimensions['C'].width = 12  # Amount
    ws.column_dimensions['D'].width = 30  # Upload Date (date only)
    ws.column_dimensions['E'].width = 60  # Comment (wider to avoid overlap)
    ws.column_dimensions['F'].width = 12  # View
    ws.column_dimensions['G'].width = 14  # Download

    # Build response
    from django.utils import timezone as _tz
    ts = _tz.now().strftime('%Y-%m-%d_%H-%M-%S')
    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    resp['Content-Disposition'] = f'attachment; filename=bills-{fy or "ALL"}-{ts}.xlsx'
    wb.save(resp)
    return resp

# Alias without dot extension to avoid 405 issues some setups produce for paths containing a dot
@api.get("/bills/export")
def export_bills_alias(request, fy: Optional[str] = None, q: Optional[str] = None, format: Optional[str] = "xlsx"):
    """Alias of /bills/export.xlsx. Call /api/bills/export?fy=YYYY-YYYY&q=term&format=xlsx
    The 'format' parameter reserved for future CSV support; currently only 'xlsx'."""
    if format.lower() != "xlsx":
        return api.create_response(request, {"detail": "Only xlsx export supported."}, status=400)
    return export_bills_xlsx(request, fy=fy, q=q)

# Extra alias variants for environments or proxies that mishandle dotted paths or strict slashes
@api.get("/bills/export/")
def export_bills_alias_slash(request, fy: Optional[str] = None, q: Optional[str] = None):
    return export_bills_xlsx(request, fy=fy, q=q)

@api.get("/bills/export-file")
def export_bills_alias_file(request, fy: Optional[str] = None, q: Optional[str] = None):
    return export_bills_xlsx(request, fy=fy, q=q)

@api.get("/bills/export-xlsx")
def export_bills_alias_xlsx(request, fy: Optional[str] = None, q: Optional[str] = None):
    return export_bills_xlsx(request, fy=fy, q=q)

# Completely distinct path (no nested 'bills/' segment) to bypass any unexpected route collisions
@api.get("/bills-export")
def export_bills_top_level(request, fy: Optional[str] = None, q: Optional[str] = None):
    """Extra safety alias. Try /api/bills-export if other aliases return 405.
    Adds an identifying header so we can confirm which endpoint responded."""
    resp = export_bills_xlsx(request, fy=fy, q=q)
    try:
        resp["X-Export-Endpoint"] = "bills-export"
    except Exception:
        pass
    return resp