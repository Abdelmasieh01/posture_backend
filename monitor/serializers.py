"""
Input validation and JSON serialization for the monitor REST API and WebSockets.
Exists to validate types, required fields, image constraints, and choices before saving.
Called by views.py and consumers.py on incoming payloads.
"""
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers
from .models import AlertEvent, LatestStatus, MinuteAggregate, Snapshot

VALID_COMMANDS = ["calibrate_camera", "calibrate_distance"]
VALID_COMMAND_STATUSES = ["get_ready", "recording", "done", "failed"]
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 2 MB limit to protect SD card / disk space


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


class CommandSerializer(serializers.Serializer):
    """Validates POST /api/commands/ requests from the Flutter app."""
    command = serializers.ChoiceField(choices=VALID_COMMANDS)


class CommandResultSerializer(serializers.Serializer):
    """Validates calibration progress messages sent by the Pi over /ws/device/."""
    type = serializers.ChoiceField(choices=["command_result"])
    command = serializers.ChoiceField(choices=VALID_COMMANDS)
    status = serializers.ChoiceField(choices=VALID_COMMAND_STATUSES)
    detail = serializers.CharField(required=False, allow_blank=True, default="")


class SnapshotSerializer(serializers.ModelSerializer):
    """Validates multipart JPEG uploads and serializes Snapshot rows with absolute image_url."""
    image = serializers.ImageField(write_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Snapshot
        fields = ["id", "event_id", "ts", "label", "image", "image_url", "created_at"]
        read_only_fields = ["id", "image_url", "created_at"]

    def validate_image(self, uploaded_file):
        # Reject files larger than 2 MB before processing
        if uploaded_file.size > MAX_SNAPSHOT_BYTES:
            raise serializers.ValidationError("Image size must not exceed 2 MB.")

        # Verify actual image headers with Pillow rather than trusting the filename extension
        try:
            uploaded_file.seek(0)
            with Image.open(uploaded_file) as img:
                img_format = img.format
            uploaded_file.seek(0)
        except (UnidentifiedImageError, OSError):
            raise serializers.ValidationError("Uploaded file is not a valid image.")

        if img_format != "JPEG":
            raise serializers.ValidationError("Only JPEG images are allowed.")

        return uploaded_file

    def get_image_url(self, obj) -> str:
        """Build an absolute URL (e.g. http://host:8000/media/snapshots/frame.jpg)."""
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request is not None else url

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.ts:
            data["ts"] = format_iso_utc(instance.ts)
        return data