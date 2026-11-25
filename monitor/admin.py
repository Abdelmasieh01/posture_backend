"""
Django admin registration for posture snapshots.
Exists so examiners and developers can inspect uploaded JPEG frames in /admin/.
Called automatically by Django's admin autodiscover on startup.
"""
from django.contrib import admin
from .models import Snapshot


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "event_id", "label", "ts", "image", "created_at")
    list_filter = ("label",)
    search_fields = ("event_id",)
    ordering = ("-ts", "-id")