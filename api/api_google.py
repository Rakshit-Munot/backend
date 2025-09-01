from ninja import Router, Schema
from google.oauth2 import id_token
from google.auth.transport import requests
from fastapi import HTTPException
from django.contrib.auth import get_user_model, login as auth_login
from django.http import JsonResponse
from django.core.cache import cache

router = Router()
User = get_user_model()

class TokenSchema(Schema):
    token: str

@router.post("/google-login")
def google_login(request, data: TokenSchema):
    try:
        # Check if token is cached (avoid repeated verification)
        cache_key = f"google_token:{data.token}"
        idinfo = cache.get(cache_key)

        if not idinfo:
            idinfo = id_token.verify_oauth2_token(data.token, requests.Request())
            cache.set(cache_key, idinfo, timeout=60*5)  # cache 5 minutes

        email = idinfo['email']

        # Cache user lookup
        user_cache_key = f"user:{email}"
        user = cache.get(user_cache_key)

        if not user:
            try:
                user = User.objects.get(email=email)
                cache.set(user_cache_key, user, timeout=60*5)
            except User.DoesNotExist:
                return JsonResponse({"detail": "Email not registered. Please sign up first."}, status=400)

        # Django session login
        auth_login(request, user)

        # Optional: update profile picture
        if hasattr(user, "profile") and idinfo.get("picture"):
            user.profile.picture_url = idinfo["picture"]
            user.profile.save()

        return JsonResponse({
            "message": "Login successful",
            "email": email,
            "username": user.username,
        })

    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google token")

class GoogleSignUpSchema(Schema):
    email: str
    password: str
    picture: str = ""

@router.post("/google-signup")
def google_signup(request, data: GoogleSignUpSchema):
    if User.objects.filter(email=data.email).exists():
        return JsonResponse({"detail": "Email already registered. Try logging in."}, status=400)

    username = data.email.split("@")[0]

    if User.objects.filter(username=username).exists():
        return JsonResponse({"detail": "Username already taken"}, status=400)

    user = User.objects.create_user(
        username=username,
        email=data.email,
        password=data.password,
    )

    # Optional: store picture
    if hasattr(user, "profile"):
        user.profile.picture_url = data.picture
        user.profile.save()

    # Log them in using session
    auth_login(request, user)

    return JsonResponse({
        "message": "Signup successful",
        "username": username,
        "email": data.email,
    })
