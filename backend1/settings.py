import os
from pathlib import Path
from decouple import config, Csv
import dj_database_url
from dotenv import load_dotenv
import cloudinary
from cloudinary import config as cloudinary_config
# Ensure we load the backend/.env explicitly
load_dotenv(dotenv_path=(Path(__file__).resolve().parent.parent / '.env'))
# -----------------------------------------------------------------------------
# BASE DIRECTORY
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# SECURITY
# -----------------------------------------------------------------------------
SECRET_KEY = config('SECRET_KEY')  # Load from environment

# DEBUG should be False in production
DEBUG = config('DEBUG', default=False, cast=bool)

# Allow hosts from environment (comma-separated)
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'backend-4-x6ud.onrender.com','backend-2-0k6r.onrender.com']




# HTTPS / HSTS settings (enable in production)
if not DEBUG:
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=True, cast=bool)
    CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=True, cast=bool)
    SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=3600, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = config('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True, cast=bool)
    SECURE_HSTS_PRELOAD = config('SECURE_HSTS_PRELOAD', default=True, cast=bool)

# -----------------------------------------------------------------------------
# APPLICATIONS
# -----------------------------------------------------------------------------


INSTALLED_APPS = [
    # Django
    'daphne',
    'cloudinary',
    'cloudinary_storage',
    'django_celery_beat',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'channels',
    'corsheaders',
    'django_extensions',

    # Local
    'api',
    'intruments',
]

# -----------------------------------------------------------------------------
# MIDDLEWARE
# -----------------------------------------------------------------------------
MIDDLEWARE = [
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# -----------------------------------------------------------------------------
# URL CONFIGURATION
# -----------------------------------------------------------------------------
ROOT_URLCONF = 'backend1.urls'

# -----------------------------------------------------------------------------
# TEMPLATES
# -----------------------------------------------------------------------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# -----------------------------------------------------------------------------
# WSGI & ASGI
# -----------------------------------------------------------------------------

# WSGI_APPLICATION = 'backend1.wsgi.application'
ASGI_APPLICATION = 'backend1.asgi.application'

# -----------------------------------------------------------------------------
# DATABASES (Supabase Postgres)
# -----------------------------------------------------------------------------
# print("DB SETTINGS >>>", config("DB_NAME"), config("DB_USER"), config("DB_PASSWORD"), config("DB_HOST"))

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': config('DB_PORT', default='5432'),
        'CONN_MAX_AGE': 600,
    }
}


# Cloudinary configuration
# Prefer python-decouple config(), fall back to environment vars; also support CLOUDINARY_URL
CLOUDINARY_CLOUD_NAME = config('CLOUDINARY_CLOUD_NAME', default=os.getenv('CLOUDINARY_CLOUD_NAME', ''))
CLOUDINARY_API_KEY = config('CLOUDINARY_API_KEY', default=os.getenv('CLOUDINARY_API_KEY', ''))
CLOUDINARY_API_SECRET = config('CLOUDINARY_API_SECRET', default=os.getenv('CLOUDINARY_API_SECRET', ''))

if os.getenv('CLOUDINARY_URL'):
    # If CLOUDINARY_URL is provided, Cloudinary SDK can parse it automatically
    cloudinary_config(cloudinary_url=os.getenv('CLOUDINARY_URL'), secure=True)
else:
    cloudinary_config(
        cloud_name=CLOUDINARY_CLOUD_NAME or None,
        api_key=CLOUDINARY_API_KEY or None,
        api_secret=CLOUDINARY_API_SECRET or None,
        secure=True,
    )

if DEBUG:
    # Helpful warning during development if keys are missing
    _cfg = cloudinary.config()
    if not (_cfg.cloud_name and _cfg.api_key and _cfg.api_secret):
        print("[WARN] Cloudinary credentials are not fully configured. Check .env for CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET or CLOUDINARY_URL.")
    else:
        print(f"[INFO] Cloudinary configured for cloud '{_cfg.cloud_name}'.")

# -----------------------------------------------------------------------------
# CHANNELS (Redis)
# -----------------------------------------------------------------------------
# CHANNEL_LAYERS = {
#     'default': {
#         'BACKEND': 'channels_redis.core.RedisChannelLayer',
#         'CONFIG': {
#             'hosts': [config('REDIS_URL')],
#         },
#     },
# }
# Prefer managed Redis when provided; fallback to local for development
if DEBUG:
    REDIS_URL = "redis://127.0.0.1:6379/1"
else:
    REDIS_URL = os.getenv("REDIS_URL") or os.getenv("REDIS_URL_LOCAL") or "redis://127.0.0.1:6379/1"
# Example (Render): rediss://:PASSWORD@hostname.render.com:6379

# Django Channels is designed to handle WebSockets and background communication in a multi-user, asynchronous way.

# When you want multiple clients to receive real-time updates (like your file upload list), you don’t just send a message to one WebSocket. You often need to:

# Group multiple WebSocket connections (e.g., all clients on the "file_updates" channel).

# Send a message to that group, so all connected clients get notified.

# This is where channel layers come in:

# A channel layer is like a message bus for Django Channels.

# It allows different consumers (your FileConsumer) and different server instances to communicate asynchronously.

# Common backends: Redis, In-memory (development only).

if DEBUG:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
            },
        },
    }

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}


SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"


from datetime import timedelta

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "SIGNING_KEY": SECRET_KEY,
    "ALGORITHM": "HS256",
}
# -----------------------------------------------------------------------------
# AUTH
# -----------------------------------------------------------------------------
AUTH_USER_MODEL = 'api.CustomUser'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# -----------------------------------------------------------------------------
# INTERNATIONALIZATION
# -----------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# -----------------------------------------------------------------------------
# STATIC & MEDIA FILES
# -----------------------------------------------------------------------------
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'


MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://frontend1-lake.vercel.app",
]

# Cookies & CSRF: relaxed in DEBUG, secure in production
if DEBUG:
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SAMESITE = 'Lax'
    CSRF_COOKIE_SECURE = False
else:
    SESSION_COOKIE_SAMESITE = 'None'
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SAMESITE = 'None'
    CSRF_COOKIE_SECURE = True
    # Respect proxy headers so Django treats requests as HTTPS behind Render/NGINX
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True

CSRF_TRUSTED_ORIGINS = [
    "https://frontend1-lake.vercel.app",
    "https://backend-4-x6ud.onrender.com",
    "https://backend-2-0k6r.onrender.com",
]

CORS_ALLOW_CREDENTIALS = True
CORS_PREFLIGHT_MAX_AGE = 86400

# -----------------------------------------------------------------------------
# REST FRAMEWORK
# -----------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        # 'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# -----------------------------------------------------------------------------
# EMAIL CONFIGURATION
# -----------------------------------------------------------------------------
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST')
EMAIL_PORT = config('EMAIL_PORT', cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL')
# SERVER_EMAIL = DEFAULT_FROM_EMAIL

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
ADMINS = [(config('ADMIN_NAME', default=''), config('ADMIN_EMAIL', default=''))]
MANAGERS = ADMINS
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'mail_admins': {
            'class': 'django.utils.log.AdminEmailHandler',
            'level': 'ERROR',
        },
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['mail_admins'],
            'level': 'ERROR',
            'propagate': False,
        },
        'intruments': {  # replace with your app name
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}

# -----------------------------------------------------------------------------
# DEFAULT AUTO FIELD
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
# -----------------------------------------------------------------------------
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Kolkata"

# Run Celery tasks inline during development so emails send without a worker
if DEBUG:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

