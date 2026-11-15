"""
ASGI entrypoint routing HTTP and WebSocket connections.
Exists so Daphne can serve both REST API calls and WebSocket streams in one process.
Called by the Daphne ASGI server (or `manage.py runserver`).
"""
import os
from django.core.asgi import get_asgi_application

# Initialize Django settings and populate the app registry BEFORE importing consumers or routing,
# because consumers import Django models which require apps to be loaded first.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posture_backend.settings")
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
import monitor.routing

# ProtocolTypeRouter inspects the incoming connection protocol ("http" vs "websocket")
# and hands HTTP requests to standard Django and WebSocket handshakes to Channels.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(monitor.routing.websocket_urlpatterns),
    }
)
