"""
URL patterns for the monitor app's REST API endpoints.
Exists to map /api/* paths to their corresponding view functions in views.py.
Called by posture_backend/urls.py.
"""
from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health_view, name="health"),
    path("status/", views.status_post_view, name="status-post"),
    path("status/latest/", views.status_latest_view, name="status-latest"),
    path("events/", views.events_view, name="events"),
    path("aggregates/", views.aggregates_view, name="aggregates"),
    path("summary/", views.summary_view, name="summary"),
    path("commands/", views.commands_view, name="commands"),
    path("device/", views.device_view, name="device"),
    path("snapshots/", views.snapshots_view, name="snapshots"),
]