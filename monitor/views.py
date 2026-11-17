"""
REST API endpoint implementations for health, status, events, aggregates, summary, and commands.
Exists to handle HTTP requests from the Raspberry Pi and the Flutter mobile app.
Called by Django's URL dispatcher when requests match /api/* routes.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from . import consumers
from .models import AlertEvent, LatestStatus, MinuteAggregate
from .notifications import (
    broadcast_notification,
    build_status_ws_payload,
    get_alert_message,
    send_device_command,
)
from .serializers import (
    AlertEventSerializer,
    CommandSerializer,
    MinuteAggregateSerializer,
    StatusSerializer,
    format_iso_utc,
)


def parse_iso_param(value: str | None, param_name: str) -> datetime | None:
    """Parse an ISO 8601 query parameter into a timezone-aware UTC datetime, or raise 400."""
    if not value:
        return None
    # Replace space with '+' in case URL decoding turned '+00:00' into ' 00:00'
    dt = parse_datetime(value.strip().replace(" ", "+"))
    if dt is None:
        raise ValidationError({param_name: f"Invalid ISO 8601 timestamp: '{value}'."})
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


@api_view(["GET"])
def health_view(request):
    """GET /api/health/ -> Simple liveness check for the Pi and Flutter app."""
    return Response({"ok": True, "time": format_iso_utc(timezone.now())})


@api_view(["POST"])
def status_post_view(request):
    """
    POST /api/status/ -> Upsert single LatestStatus row (pk=1) and broadcast over WebSocket.
    Does not keep historical rows so SQLite stays small and fast.
    """
    serializer = StatusSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    # Always overwrite pk=1 so there is only ever a single row in LatestStatus
    status_obj, _ = LatestStatus.objects.update_or_create(
        pk=1,
        defaults=serializer.validated_data,
    )

    # Push live status to any connected Flutter app over WebSocket
    broadcast_notification(build_status_ws_payload(status_obj))
    return Response({"ok": True}, status=status.HTTP_200_OK)


@api_view(["GET"])
def status_latest_view(request):
    """GET /api/status/latest/ -> Return the single LatestStatus row or 404 if none received."""
    status_obj = LatestStatus.objects.filter(pk=1).first()
    if status_obj is None:
        return Response({"detail": "no data yet"}, status=status.HTTP_404_NOT_FOUND)
    return Response(StatusSerializer(status_obj).data)


@api_view(["GET", "POST"])
def events_view(request):
    """
    POST /api/events/ -> Idempotently store an alert start/end event and broadcast on 'start'.
    GET /api/events/ -> Return recent alert events, newest first.
    """
    if request.method == "POST":
        serializer = AlertEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vdata = serializer.validated_data

        # Idempotency check: if the Pi retried sending an event we already saved,
        # return 200 OK immediately without duplicating the DB row or re-broadcasting.
        existing = AlertEvent.objects.filter(event_id=vdata["event_id"]).first()
        if existing is not None:
            return Response(AlertEventSerializer(existing).data, status=status.HTTP_200_OK)

        message = get_alert_message(vdata["kind"], vdata["label"])
        event = AlertEvent.objects.create(message=message, **vdata)

        # Only broadcast when an alert starts; "end" is saved for history only,
        # and the live banner clears when a status message arrives with alert=False.
        if event.state == "start":
            broadcast_notification(
                {
                    "type": "alert",
                    "data": {
                        "kind": event.kind,
                        "label": event.label,
                        "message": event.message,
                        "ts": format_iso_utc(event.ts),
                    },
                }
            )
        return Response(AlertEventSerializer(event).data, status=status.HTTP_201_CREATED)

    # GET /api/events/?limit=50&kind=&since=
    qs = AlertEvent.objects.all()

    kind = request.query_params.get("kind")
    if kind:
        qs = qs.filter(kind=kind)

    since_dt = parse_iso_param(request.query_params.get("since"), "since")
    if since_dt:
        qs = qs.filter(ts__gte=since_dt)

    limit_str = request.query_params.get("limit", "50")
    try:
        limit = int(limit_str)
        if limit < 1:
            raise ValueError
    except ValueError:
        raise ValidationError({"limit": "limit must be a positive integer."})
    limit = min(limit, 500)

    events = qs.order_by("-ts")[:limit]
    return Response(AlertEventSerializer(events, many=True).data)


@api_view(["GET", "POST"])
def aggregates_view(request):
    """
    POST /api/aggregates/ -> Upsert a per-minute summary row keyed by window_start.
    GET /api/aggregates/ -> Return per-minute rows ordered by window_start.
    """
    if request.method == "POST":
        serializer = MinuteAggregateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vdata = dict(serializer.validated_data)
        window_start = vdata.pop("window_start")

        # Upsert on window_start so network retries safely update the minute bucket
        agg, created = MinuteAggregate.objects.update_or_create(
            window_start=window_start,
            defaults=vdata,
        )
        http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(MinuteAggregateSerializer(agg).data, status=http_status)

    # GET /api/aggregates/?from=&to=
    now = timezone.now()
    from_dt = parse_iso_param(request.query_params.get("from"), "from")
    to_dt = parse_iso_param(request.query_params.get("to"), "to")

    if to_dt is None:
        to_dt = now
    if from_dt is None:
        from_dt = to_dt - timedelta(minutes=60)

    aggs = MinuteAggregate.objects.filter(
        window_start__gte=from_dt, window_start__lte=to_dt
    ).order_by("window_start")[:1000]
    return Response(MinuteAggregateSerializer(aggs, many=True).data)


def floor_to_bucket(dt: datetime, granularity: str) -> datetime:
    """Truncate a UTC datetime down to the start of its hour or day."""
    dt = dt.astimezone(dt_timezone.utc)
    if granularity == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


@api_view(["GET"])
def summary_view(request):
    """
    GET /api/summary/?granularity=hour|day&from=&to=
    Aggregates MinuteAggregate and AlertEvent rows into hourly or daily buckets in pure Python.
    Computing in Python avoids SQLite-specific JSON queries and guarantees portable math.
    """
    granularity = request.query_params.get("granularity", "hour")
    if granularity not in ("hour", "day"):
        raise ValidationError({"granularity": "Must be 'hour' or 'day'."})

    now = timezone.now()
    to_dt = parse_iso_param(request.query_params.get("to"), "to") or now
    if request.query_params.get("from"):
        from_dt = parse_iso_param(request.query_params.get("from"), "from")
    else:
        from_dt = to_dt - (timedelta(hours=24) if granularity == "hour" else timedelta(days=7))

    if from_dt > to_dt:
        raise ValidationError({"from": "'from' timestamp must be earlier than 'to'."})

    step = timedelta(hours=1) if granularity == "hour" else timedelta(days=1)
    start_bucket = floor_to_bucket(from_dt, granularity)
    end_bucket = floor_to_bucket(to_dt, granularity)

    # Pre-build every bucket between start_bucket and end_bucket with zeroed accumulators
    # so the Flutter charts receive a continuous series with no missing time gaps.
    buckets = {}
    cursor = start_bucket
    while cursor <= end_bucket:
        buckets[cursor] = {
            "seconds_per_label": {},
            "weighted_dist_sum": 0.0,
            "dist_weight_total": 0,
            "seconds_too_close": 0.0,
            "alert_count": 0,
        }
        cursor += step

    query_end = end_bucket + step
    aggs = MinuteAggregate.objects.filter(
        window_start__gte=start_bucket, window_start__lt=query_end
    )
    for agg in aggs:
        b_key = floor_to_bucket(agg.window_start, granularity)
        if b_key not in buckets:
            continue
        b = buckets[b_key]
        for label, secs in (agg.seconds_per_label or {}).items():
            b["seconds_per_label"][label] = b["seconds_per_label"].get(label, 0.0) + float(secs)
        b["seconds_too_close"] += float(agg.seconds_too_close or 0.0)
        # Weight each minute's average distance by the number of detections in that minute
        if agg.avg_distance_cm is not None and agg.detections > 0:
            b["weighted_dist_sum"] += float(agg.avg_distance_cm) * agg.detections
            b["dist_weight_total"] += agg.detections

    # Count only "start" alert events in each bucket
    alert_starts = AlertEvent.objects.filter(
        state="start", ts__gte=start_bucket, ts__lt=query_end
    )
    for ev in alert_starts:
        b_key = floor_to_bucket(ev.ts, granularity)
        if b_key in buckets:
            buckets[b_key]["alert_count"] += 1

    # Format final bucket list
    result = []
    for b_key, b in buckets.items():
        spl = b["seconds_per_label"]
        # Calculate good_percent excluding "unknown" seconds
        known_total = sum(secs for lbl, secs in spl.items() if lbl != "unknown")
        good_secs = spl.get("goodposture", 0.0)
        good_percent = round((good_secs / known_total) * 100.0, 2) if known_total > 0 else None

        avg_dist = (
            round(b["weighted_dist_sum"] / b["dist_weight_total"], 2)
            if b["dist_weight_total"] > 0
            else None
        )

        result.append(
            {
                "bucket_start": format_iso_utc(b_key),
                "seconds_per_label": spl,
                "good_percent": good_percent,
                "avg_distance_cm": avg_dist,
                "seconds_too_close": round(b["seconds_too_close"], 2),
                "alert_count": b["alert_count"],
            }
        )

    return Response(result)


@api_view(["POST"])
def commands_view(request):
    """
    POST /api/commands/ -> Forward a live calibration command to the connected Pi.
    Commands are NOT stored in the database and NOT queued: a calibration requires the user
    to be sitting in front of the camera right now, so replaying an old queued command later
    when the Pi reconnects would calibrate against an empty chair or wrong posture.
    """
    serializer = CommandSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    if consumers.connected_devices == 0:
        return Response(
            {"detail": "Pi not connected"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    command_name = serializer.validated_data["command"]
    send_device_command(command_name)
    return Response(
        {"sent": True, "command": command_name},
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
def device_view(request):
    """GET /api/device/ -> Report whether at least one Raspberry Pi agent is connected."""
    return Response({"connected": consumers.connected_devices > 0})