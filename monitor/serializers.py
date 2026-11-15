"""
Input validation and JSON serialization for the monitor REST API.
Exists to validate types, required fields, and ranges before touching the database.
Called by views.py on every POST and GET request.
"""
from rest_framework import serializers
from .models import AlertEvent, LatestStatus, MinuteAggregate


def format_iso_utc(dt) -> str:
    """Format a timezone-aware datetime as a clean ISO 8601 UTC string ending in 'Z'."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class StatusSerializer(serializers.Serializer):
    """Validates POST /api/status/ payloads and formats LatestStatus responses."""
    # Note: DRF Serializers ignore unknown extra fields in request.data by default.
    ts = serializers.DateTimeField()
    label = serializers.CharField(max_length=64)
    confidence = serializers.FloatField(required=False, allow_null=True, default=None)
    distance_cm = serializers.FloatField(required=False, allow_null=True, default=None)
    distance_state = serializers.CharField(max_length=32)
    posture_alert = serializers.BooleanField()
    distance_alert = serializers.BooleanField()
    received_at = serializers.DateTimeField(read_only=True)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Ensure consistent UTC 'Z' formatting for timestamps
        if instance.ts:
            data["ts"] = format_iso_utc(instance.ts)
        if getattr(instance, "received_at", None):
            data["received_at"] = format_iso_utc(instance.received_at)
        return data


class AlertEventSerializer(serializers.ModelSerializer):
    """Validates POST /api/events/ payloads and serializes AlertEvent rows."""
    # We omit UniqueValidator on event_id here because views.py handles duplicate event_ids
    # idempotently (returning 200 OK instead of a 400 validation error).
    event_id = serializers.CharField(max_length=64)
    kind = serializers.ChoiceField(choices=["posture", "distance"])
    state = serializers.ChoiceField(choices=["start", "end"])
    duration_s = serializers.FloatField(required=False, allow_null=True, default=None)

    class Meta:
        model = AlertEvent
        fields = [
            "event_id",
            "ts",
            "kind",
            "state",
            "label",
            "duration_s",
            "message",
            "created_at",
        ]
        read_only_fields = ["message", "created_at"]


class MinuteAggregateSerializer(serializers.ModelSerializer):
    """Validates POST /api/aggregates/ payloads and serializes MinuteAggregate rows."""
    # Explicitly declare window_start without UniqueValidator so views.py can upsert
    # existing window_start timestamps via update_or_create without a 400 error.
    window_start = serializers.DateTimeField()
    window_seconds = serializers.IntegerField(required=False, default=60, min_value=1)
    seconds_per_label = serializers.DictField(child=serializers.FloatField(min_value=0))
    avg_distance_cm = serializers.FloatField(required=False, allow_null=True, default=None)
    min_distance_cm = serializers.FloatField(required=False, allow_null=True, default=None)
    seconds_too_close = serializers.FloatField(required=False, default=0.0, min_value=0)
    detections = serializers.IntegerField(min_value=0)
    avg_angles = serializers.DictField(
        child=serializers.FloatField(), required=False, allow_null=True, default=None
    )

    class Meta:
        model = MinuteAggregate
        fields = [
            "window_start",
            "window_seconds",
            "seconds_per_label",
            "avg_distance_cm",
            "min_distance_cm",
            "seconds_too_close",
            "detections",
            "avg_angles",
            "created_at",
        ]
        read_only_fields = ["created_at"]
