"""
WebSocket URL routing for the monitor app.
Exists to map the ws://<host>:8000/ws/notifications/ path to NotificationConsumer.
Called by posture_backend/asgi.py when setting up the ProtocolTypeRouter.
"""
from django.urls import path
from .consumers import NotificationConsumer

websocket_urlpatterns = [
    path("ws/notifications/", NotificationConsumer.as_asgi()),
]
