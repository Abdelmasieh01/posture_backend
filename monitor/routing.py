"""
WebSocket URL routing for the monitor app.
Exists to map /ws/notifications/ (app) and /ws/device/ (Pi) to their Channels consumers.
Called by posture_backend/asgi.py when setting up the ProtocolTypeRouter.
"""
from django.urls import path
from .consumers import DeviceConsumer, NotificationConsumer

websocket_urlpatterns = [
    path("ws/notifications/", NotificationConsumer.as_asgi()),
    path("ws/device/", DeviceConsumer.as_asgi()),
]