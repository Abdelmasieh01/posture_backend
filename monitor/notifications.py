"""
Alert message dictionary and WebSocket broadcast helper.
Exists to keep human-readable notification strings and Channels group_send logic in one place.
Called by views.py when status updates or new alert events arrive from the Pi.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# Single dictionary mapping posture/distance labels to user-friendly coaching advice
ALERT_MESSAGES = {
    "forwardbadposture": "You are leaning forward. Sit back and straighten your back.",
    "backwardbadposture": "You are leaning back too far. Sit upright.",
    "headforward": "Your head is too far forward. Bring it back over your shoulders.",
    "tooclose": "You are too close to the screen. Move back.",
}
DEFAULT_ALERT_MESSAGE = "Bad posture detected."

# Single Channels group name shared by all connected Flutter clients
NOTIFICATION_GROUP = "notifications"


def get_alert_message(kind: str, label: str) -> str:
    """Return the human-readable alert text for a given alert kind and label."""
    if kind == "distance" or label == "tooclose":
        return ALERT_MESSAGES["tooclose"]
    return ALERT_MESSAGES.get(label, DEFAULT_ALERT_MESSAGE)


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
