"""
Root HTTP URL router for the posture_backend project.
Exists to mount the monitor app's REST endpoints under the /api/ prefix.
Called by Django's HTTP handler on every incoming HTTP request.
"""
from django.urls import include, path

urlpatterns = [
    path("api/", include("monitor.urls")),
]
