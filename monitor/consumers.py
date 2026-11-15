"""
Django Channels WebSocket consumer for real-time posture and alert notifications.
Exists to stream live status updates and alerts to connected Flutter mobile apps.
Called by the ASGI router when a client connects to ws://<host>:8000/ws/notifications/.
"""
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import LatestStatus
from .notifications import NOTIFICATION_GROUP
from .views import build_status_ws_payload


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """
    Push-only WebSocket consumer.
    Joins the 'notifications' group on connect, immediately sends the latest status if one
    exists in the database, and ignores any messages sent by the client.
    """

    async def connect(self):
        # Subscribe this WebSocket connection to the shared broadcast group
        await self.channel_layer.group_add(NOTIFICATION_GROUP, self.channel_name)
        await self.accept()

        # Immediately send the latest status on connect so the Flutter app UI
        # doesn't have to wait up to 5 seconds for the next Pi heartbeat.
        initial_payload = await self._get_latest_status_payload()
        if initial_payload is not None:
            await self.send_json(initial_payload)

    async def disconnect(self, close_code):
        # Remove this connection from the broadcast group when the app closes or disconnects
        await self.channel_layer.group_discard(NOTIFICATION_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # The Flutter app only receives data; ignore anything sent from the client
        pass

    async def notify_message(self, event):
        """
        Handler for 'notify_message' events sent via channel_layer.group_send().
        Forwards the inner payload dictionary directly to the WebSocket client as JSON.
        """
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _get_latest_status_payload(self):
        """Query the synchronous Django ORM safely from an async consumer using a thread."""
        latest = LatestStatus.objects.filter(pk=1).first()
        if latest is None:
            return None
        return build_status_ws_payload(latest)
