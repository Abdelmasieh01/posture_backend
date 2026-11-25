"""
Database schema definitions for status, alert events, minute aggregates, and snapshots.
Exists to persist Raspberry Pi telemetry and JPEG frames in SQLite using Django's ORM.
Called by serializers, views, admin, WebSocket consumers, and management commands.
"""
from django.db import models


class LatestStatus(models.Model):
    """
    Stores only the single most recent status reading (always pk=1).
    We overwrite pk=1 instead of inserting new rows so the database doesn't fill up
    with 17,280 status rows per day when the Pi posts every 5 seconds.
    """
    ts = models.DateTimeField()
    label = models.CharField(max_length=64)
    confidence = models.FloatField(null=True, blank=True)
    distance_cm = models.FloatField(null=True, blank=True)
    distance_state = models.CharField(max_length=32)
    posture_alert = models.BooleanField()
    distance_alert = models.BooleanField()
    # auto_now updates received_at to current server time on every save()
    received_at = models.DateTimeField(auto_now=True)


class AlertEvent(models.Model):
    """
    Stores discrete alert transitions ("start" and "end") sent by the Pi.
    Each event carries a Pi-generated UUID (`event_id`) marked unique so that if Wi-Fi drops
    and the Pi retries the exact same POST, we don't store duplicate alerts.
    """
    event_id = models.CharField(max_length=64, unique=True)
    # Indexed because GET /api/events/ and GET /api/summary/ filter and sort by `ts`
    ts = models.DateTimeField(db_index=True)
    kind = models.CharField(max_length=32)
    state = models.CharField(max_length=16)
    label = models.CharField(max_length=64)
    duration_s = models.FloatField(null=True, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class MinuteAggregate(models.Model):
    """
    Stores one pre-aggregated summary row per minute of monitoring.
    `window_start` is unique and indexed so the Pi can safely retry posting a minute bucket
    (using update_or_create) and the app can quickly query ranges for charts.
    """
    window_start = models.DateTimeField(unique=True, db_index=True)
    window_seconds = models.IntegerField(default=60)
    seconds_per_label = models.JSONField()
    avg_distance_cm = models.FloatField(null=True, blank=True)
    min_distance_cm = models.FloatField(null=True, blank=True)
    seconds_too_close = models.FloatField(default=0.0)
    detections = models.IntegerField()
    avg_angles = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Snapshot(models.Model):
    """
    Stores a single JPEG frame captured by the Pi during an active bad-posture alert.
    `event_id` links the frame to the alert's "start" event UUID without a strict ForeignKey,
    so a snapshot arriving slightly before or out of order with /api/events/ still saves cleanly.
    """
    event_id = models.UUIDField(db_index=True)
    ts = models.DateTimeField(db_index=True)
    label = models.CharField(max_length=64)
    image = models.ImageField(upload_to="snapshots/")
    created_at = models.DateTimeField(auto_now_add=True)