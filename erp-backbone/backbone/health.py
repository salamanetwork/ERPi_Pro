from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM django_migrations LIMIT 1")
            available = cursor.fetchone() is not None
    except DatabaseError:
        available = False
    available = available and (settings.BASE_DIR / "frontend/dist/index.html").is_file()
    return JsonResponse({"status": "ready" if available else "unavailable"}, status=200 if available else 503)
