"""
Root HTTP URL router for the posture_backend project.
Exists to mount /admin/, /api/, and local /media/ file serving for the demo.
Called by Django's HTTP handler on every incoming HTTP request.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("monitor.urls")),
]

# Serve uploaded snapshot JPEGs directly from MEDIA_ROOT during the local demo
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)