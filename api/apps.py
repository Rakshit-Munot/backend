from django.apps import AppConfig
from django.core.cache import cache
from django.conf import settings


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        # Best-effort one-time warm-up when the app starts.
        # Guard with cache.add so it runs once per process cluster window.
        try:
            # Avoid triggering DB work at app init when Celery runs tasks eagerly (DEBUG=True)
            eager = bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
            if eager:
                return
            if cache.add("warmup:api_once", True, timeout=60):
                try:
                    from intruments.tasks import warm_instruments_cache
                    from api.tasks import warm_bills_handouts_cache
                    warm_instruments_cache.delay()
                    warm_bills_handouts_cache.delay()
                except Exception:
                    # Celery or import might not be ready yet; ignore
                    pass
        except Exception:
            pass
