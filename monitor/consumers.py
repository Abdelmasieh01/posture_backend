"""
Django Channels WebSocket consumers for the Flutter app and the Raspberry Pi agent.
Exists to stream live posture updates, alerts, device status, and calibration commands.
Called by the ASGI router on /ws/notifications/ and /ws/device/.
"""
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from .models import LatestStatus
from .notifications import DEVICE_GROUP, NOTIFICATION_GROUP, build_status_ws_payload
from .serializers import CommandResultSerializer, format_iso_utc

# Module-level in-memory counter of currently connected Pi WebSocket clients.
# This is valid ONLY because we run a single ASGI process (Daphne + InMemoryChannelLayer);
# multiple worker processes would not share Python module globals.
connected_devices = 0


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """
    Push-only WebSocket consumer for the Flutter mobile app (/ws/notifications/).
    Joins 'notifications', immediately pushes LatestStatus (if any) and Pi connection state.
    """

    async def connect(self):
        # Subscribe this WebSocket connection to the shared app broadcast group
        await self.channel_layer.group_add(NOTIFICATION_GROUP, self.channel_name)
        await self.accept()

        # Immediately send the latest status on connect so the Flutter app UI
        # doesn't have to wait up to 5 seconds for the next Pi heartbeat.
        initial_payload = await self._get_latest_status_payload()
        if initial_payload is not None:
            await self.send_json(initial_payload)

        # Also tell the newly connected app whether the Raspberry Pi is currently online
        await self.send_json(
            {"type": "device", "data": {"connected": connected_devices > 0}}
        )

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


class DeviceConsumer(AsyncJsonWebsocketConsumer):
    """
    Bidirectional WebSocket consumer for the Raspberry Pi agent (/ws/device/).
    Receives calibration progress from the Pi and forwards calibration commands to the Pi.
    """

    async def connect(self):
        global connected_devices
        await self.channel_layer.group_add(DEVICE_GROUP, self.channel_name)
        await self.accept()
        connected_devices += 1

        # Notify any open Flutter app immediately that the Pi is online
        await self.channel_layer.group_send(
            NOTIFICATION_GROUP,
            {
                "type": "notify_message",
                "payload": {"type": "device", "data": {"connected": connected_devices > 0}},
            },
        )

    async def disconnect(self, close_code):
        global connected_devices
        await self.channel_layer.group_discard(DEVICE_GROUP, self.channel_name)
        # Clamp at 0 as a safety guard against unexpected double-disconnects
        connected_devices = max(0, connected_devices - 1)

        # Notify any open Flutter app immediately that the Pi went offline
        await self.channel_layer.group_send(
            NOTIFICATION_GROUP,
            {
                "type": "notify_message",
                "payload": {"type": "device", "data": {"connected": connected_devices > 0}},
            },
        )

    async def receive_json(self, content, **kwargs):
        """Validate Pi command_result messages with DRF and forward valid ones to the app."""
        if not isinstance(content, dict):
            return
        serializer = CommandResultSerializer(data=content)
        if not serializer.is_valid():
            # Silently ignore malformed or unknown messages from the Pi
            return

        vdata = serializer.validated_data
        await self.channel_layer.group_send(
            NOTIFICATION_GROUP,
            {
                "type": "notify_message",
                "payload": {
                    "type": "command_result",
                    "data": {
                        "command": vdata["command"],
                        "status": vdata["status"],
                        "detail": vdata["detail"],
                        "ts": format_iso_utc(timezone.now()),
                    },
                },
            },
        )

    async def device_command(self, event):
        """Handler for 'device_command' group events sent from POST /api/commands/."""
        await self.send_json({"type": "command", "command": event["command"]})