"""
Alert message dictionary and WebSocket broadcast helpers for app and Pi groups.
Exists to keep notification strings and Channels group_send logic in one place.
Called by views.py and consumers.py when pushing status, alerts, or calibration commands.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .serializers import format_iso_utc

# Single dictionary mapping posture/distance labels to user-friendly coaching advice
ALERT_MESSAGES = {
    "forwardbadposture": "You are leaning forward. Sit back and straighten your back.",
    "backwardbadposture": "You are leaning back too far. Sit upright.",
    "headforward": "Your head is too far forward. Bring it back over your shoulders.",
    "tooclose": "You are too close to the screen. Move back.",
}
DEFAULT_ALERT_MESSAGE = "Bad posture detected."

# Channels group names: one for connected Flutter apps, one for the Raspberry Pi agent
NOTIFICATION_GROUP = "notifications"
DEVICE_GROUP = "device"


def get_alert_message(kind: str, label: str) -> str:
    """Return the human-readable alert text for a given alert kind and label."""
    if kind == "distance" or label == "tooclose":
        return ALERT_MESSAGES["tooclose"]
    return ALERT_MESSAGES.get(label, DEFAULT_ALERT_MESSAGE)


def build_status_ws_payload(status_obj) -> dict:
    """Format a LatestStatus row into the exact dictionary expected by WebSocket clients."""
    return {
        "type": "status",
        "data": {
            "ts": format_iso_utc(status_obj.ts),
            "label": status_obj.label,
            "confidence": status_obj.confidence,
            "distance_cm": status_obj.distance_cm,
            "distance_state": status_obj.distance_state,
            "posture_alert": status_obj.posture_alert,
            "distance_alert": status_obj.distance_alert,
        },
    }


def broadcast_notification(payload: dict) -> None:
    """
    Send a JSON payload to all WebSocket clients in the 'notifications' group.

    Why async_to_sync works here:
    Django REST Framework views run synchronously in a thread pool, whereas Django Channels'
    channel layer (`group_send`) is an async coroutine running on the ASGI event loop.
    `async_to_sync` bridges the two by submitting the `group_send` coroutine to the running
    ASGI event loop and blocking the synchronous view thread for a fraction of a millisecond
    until the message is queued in memory.
    """
    channel_layer = get_channel_layer()
    if channel_layer is not None:
        # "notify_message" maps to the `notify_message` method on NotificationConsumer
        async_to_sync(channel_layer.group_send)(
            NOTIFICATION_GROUP,
            {
                "type": "notify_message",
                "payload": payload,
            },
        )


def send_device_command(command: str) -> None:
    """
    Send a calibration command to the connected Raspberry Pi in the 'device' group.
    Uses async_to_sync for the same reason as broadcast_notification: bridging sync DRF
    views to the async Channels layer.
    """
    channel_layer = get_channel_layer()
    if channel_layer is not None:
        # "device_command" maps to the `device_command` method on DeviceConsumer
        async_to_sync(channel_layer.group_send)(
            DEVICE_GROUP,
            {
                "type": "device_command",
                "command": command,
            },
        )