# Posture Monitor Demo Backend (`posture_backend`)

A lightweight, single-process Django 5 + Django REST Framework + Django Channels backend for a Raspberry Pi posture and screen-distance monitoring school demo.

---

## 1. How to Install and Run

```bash
# 1. Create and activate a Python 3.11+ virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Create database tables in db.sqlite3
python manage.py makemigrations monitor
python manage.py migrate

# 4. (Optional) Seed 24 hours of demo chart & alert data for the Flutter app
python manage.py seed_demo_data --hours 24

# 5. Run the automated test suite
python manage.py test

# 6. Start the ASGI server on all network interfaces (port 8000)
daphne -b 0.0.0.0 -p 8000 posture_backend.asgi:application
# Or during development (also uses Daphne because daphne is first in INSTALLED_APPS):
python manage.py runserver 0.0.0.0:8000
```

To simulate the Raspberry Pi in a second terminal:

```bash
python tools/fake_pi.py --base http://localhost:8000 --fast
```

---

## 2. REST API Endpoints & `curl` Examples

| Method | Path | Caller | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/health/` | Pi / App | Liveness check returning current server UTC time. |
| `POST` | `/api/status/` | Pi (every ~5s) | Upserts the single `LatestStatus` row (`pk=1`) and broadcasts a WebSocket `"status"` message. |
| `GET` | `/api/status/latest/` | App | Returns the latest status JSON, or `404 {"detail": "no data yet"}`. |
| `POST` | `/api/events/` | Pi | Idempotently saves an alert `"start"` or `"end"` event (`201` on create, `200` on duplicate `event_id`). Broadcasts WebSocket `"alert"` on `"start"`. |
| `GET` | `/api/events/` | App | Lists recent alert events (newest first). Supports `?limit=50&kind=&since=`. |
| `POST` | `/api/aggregates/` | Pi (1/min) | Upserts a `MinuteAggregate` row keyed by `window_start`. |
| `GET` | `/api/aggregates/` | App | Lists per-minute rows ordered by `window_start`. Supports `?from=&to=`. |
| `GET` | `/api/summary/` | App | Computes hourly or daily chart buckets in Python (`?granularity=hour|day&from=&to=`). |

### `curl` Examples

**Health check:**
```bash
curl -s http://localhost:8000/api/health/
```

**Post live status (from Pi):**
```bash
curl -i -X POST http://localhost:8000/api/status/ \
  -H "Content-Type: application/json" \
  -d '{
    "ts": "2026-05-10T14:00:00Z",
    "label": "forwardbadposture",
    "confidence": 0.91,
    "distance_cm": 52.3,
    "distance_state": "ok",
    "posture_alert": true,
    "distance_alert": false
  }'
```

**Get latest status (from App):**
```bash
curl -s http://localhost:8000/api/status/latest/
```

**Post an alert start event (from Pi):**
```bash
curl -i -X POST http://localhost:8000/api/events/ \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "550e8400-e29b-41d4-a716-446655440000",
    "ts": "2026-05-10T14:00:08Z",
    "kind": "posture",
    "state": "start",
    "label": "forwardbadposture",
    "duration_s": null
  }'
```

**Get recent alert events (from App):**
```bash
curl -s "http://localhost:8000/api/events/?limit=10&kind=posture&since=2026-05-10T00:00:00Z"
```

**Post a minute aggregate (from Pi):**
```bash
curl -i -X POST http://localhost:8000/api/aggregates/ \
  -H "Content-Type: application/json" \
  -d '{
    "window_start": "2026-05-10T14:00:00Z",
    "window_seconds": 60,
    "seconds_per_label": {"goodposture": 41, "forwardbadposture": 12, "unknown": 7},
    "avg_distance_cm": 56.4,
    "min_distance_cm": 47.2,
    "seconds_too_close": 0,
    "detections": 15,
    "avg_angles": {
      "neck_angle": 21.3,
      "torso_angle": 3.1,
      "alignment_angle": 171.2,
      "head_offset": 0.08,
      "head_pitch": 12.5
    }
  }'
```

**Get per-minute aggregates (from App):**
```bash
curl -s "http://localhost:8000/api/aggregates/?from=2026-05-10T13:00:00Z&to=2026-05-10T14:05:00Z"
```

**Get hourly or daily summary buckets (from App):**
```bash
curl -s "http://localhost:8000/api/summary/?granularity=hour"
curl -s "http://localhost:8000/api/summary/?granularity=day"
```

---

## 3. How to Test the WebSocket

Connect to `ws://localhost:8000/ws/notifications/`. Upon connection, the server immediately sends the current `LatestStatus` (if one exists) and then streams live `"status"` and `"alert"` JSON messages.

### Option A: Python snippet using `websockets`

```python
import asyncio
import websockets

async def listen():
    uri = "ws://localhost:8000/ws/notifications/"
    async with websockets.connect(uri) as ws:
        print("Connected! Waiting for messages...")
        async for message in ws:
            print("Received:", message)

asyncio.run(listen())
```

### Option B: Using `websocat` from the terminal

```bash
websocat ws://localhost:8000/ws/notifications/
```

---

## 4. End-to-End Data Flow

1. **Camera Analysis on the Pi:** The Raspberry Pi analyses the user's posture and screen distance locally and sends JSON telemetry over plain HTTP (`POST /api/status/`, `/api/events/`, `/api/aggregates/`) to Daphne on port 8000.
2. **ASGI Protocol Routing:** Daphne's `ProtocolTypeRouter` directs HTTP requests to Django REST Framework views and WebSocket connections (`/ws/notifications/`) to `NotificationConsumer`.
3. **Validation & SQLite Persistence:** DRF serializers validate all fields and save data to SQLite—overwriting `pk=1` for `LatestStatus`, deduplicating `AlertEvent` by `event_id`, and upserting `MinuteAggregate` by `window_start`.
4. **Sync-to-Async Channel Bridge:** When a status update or `"start"` alert is saved, the synchronous DRF view calls `async_to_sync(channel_layer.group_send)("notifications", ...)` to push the payload into the `InMemoryChannelLayer`.
5. **WebSocket Broadcast to Flutter:** Every connected `NotificationConsumer` receives the group event and pushes the JSON frame over the open WebSocket to the Flutter mobile app.
6. **App UI Updates & Dashboard Reads:** The Flutter app displays or clears alert banners immediately from WebSocket frames, and fetches historical charts via `GET /api/summary/` and `GET /api/events/`.

---

## 5. Design Decisions and Why

- **No authentication or permissions:** The system operates with a single Raspberry Pi and a single user on a trusted local Wi-Fi router. Omitting tokens and user tables keeps the code concise and removes login friction during the live school presentation.
- **In-memory channel layer (`InMemoryChannelLayer`):** Storing WebSocket groups inside process RAM avoids installing and running a separate Redis server on the Raspberry Pi while providing sub-millisecond broadcast latency.
- **Per-minute aggregates (`MinuteAggregate`):** Instead of storing raw frame-by-frame pose detections on the backend, the Pi rolls metrics into 60-second buckets. That keeps the database tiny (1,440 rows/day) and makes hourly/daily chart queries instantaneous.
- **Idempotent upserts (`event_id` and `window_start`):** Wi-Fi connections can drop after the server processes a request but before the Pi receives the HTTP response. Using a Pi-generated UUID (`event_id`) on `AlertEvent` and `update_or_create` on `MinuteAggregate.window_start` guarantees that retries never create duplicate alerts or double-counted minutes.
- **Status not stored historically (`LatestStatus` `pk=1`):** The Pi posts live status every 5 seconds. Overwriting a single row (`pk=1`) gives new WebSocket clients an immediate snapshot without bloating SQLite with 17,280 status rows per day.

---

## 6. Limitations

- **Notifications only reach an open app:** Because WebSockets require an active TCP connection and we do not use Firebase Cloud Messaging (FCM), the phone only receives live alerts while the Flutter app is open and connected to the LAN.
- **Single-process only:** `InMemoryChannelLayer` stores state in Python process memory. Running multiple worker processes would isolate their memory spaces, so HTTP posts handled by Worker A would not broadcast to WebSockets connected to Worker B.
- **No authentication:** Any device on the same local network can post fake data or listen to the WebSocket stream.
- **Plain HTTP / WS:** Traffic is unencrypted on the local network (`http://` and `ws://` rather than `https://` and `wss://`).

---

## 7. Assumptions

- All timestamps sent by the Pi or passed in query parameters (`since`, `from`, `to`) are valid ISO 8601 strings; if a timestamp lacks a timezone offset, it is assumed to be UTC.
- `GET /api/summary/` floors `from` and `to` timestamps down to the start of their respective hour (`HH:00:00Z`) or day (`00:00:00Z`) so every bucket aligns cleanly to clock boundaries.
- Unknown extra JSON fields sent in `POST` bodies are silently ignored by Django REST Framework serializers so minor Pi payload additions never break the backend.
- The `migrations/` folder includes `0001_initial.py` so `python manage.py migrate` works immediately without needing `makemigrations` first.

---

## 8. Likely Examiner Questions & Answers

1. **Why must `"daphne"` be placed at the very top of `INSTALLED_APPS`?**
   Django apps override management commands in the order they appear in `INSTALLED_APPS`. Putting `"daphne"` first replaces Django's default WSGI-only `runserver` command with Daphne's ASGI `runserver`, allowing both HTTP and WebSockets to work seamlessly during development.
2. **Why do we call `django.core.asgi.get_asgi_application()` in `asgi.py` before importing `monitor.routing`?**
   `monitor.routing` imports `NotificationConsumer`, which imports `LatestStatus` from `monitor.models`. Django models cannot be imported until `django.setup()` has populated the app registry, which `get_asgi_application()` executes.
3. **How can a synchronous DRF view broadcast to an asynchronous WebSocket consumer?**
   We wrap `channel_layer.group_send(...)` in `asgiref.sync.async_to_sync()`. This helper schedules the async `group_send` coroutine onto the main ASGI event loop and waits for it to queue the message before returning control to the synchronous HTTP view thread.
4. **Why don't we broadcast a WebSocket `"alert"` message when an alert `"end"` event is posted?**
   The mobile app clears its active alert banner automatically as soon as it receives a regular `"status"` WebSocket message with `posture_alert: false` and `distance_alert: false`. The `"end"` event only needs to be recorded in SQLite so we know `duration_s` for historical logs.
5. **How does the backend calculate `good_percent` in `GET /api/summary/`?**
   For each hour or day bucket, Python sums all values in `seconds_per_label` except `"unknown"`. If that known total is greater than zero, `good_percent = (goodposture_seconds / known_total_seconds) * 100`; otherwise it returns `null` to avoid division by zero.
6. **Why is `avg_distance_cm` in `/api/summary/` a weighted average instead of a simple mean of minutes?**
   A minute where the user sat in front of the camera for 30 detections carries more real data than a minute where the user walked away after 2 detections. Weighting each minute's `avg_distance_cm` by its `detections` count produces the true average distance across all detected frames.
7. **What happens if the Raspberry Pi loses Wi-Fi briefly and sends the same `POST /api/events/` request twice?**
   Before creating a row, `events_view` checks `AlertEvent.objects.filter(event_id=...).first()`. If the UUID already exists, the backend returns `200 OK` with the existing row and skips both database insertion and WebSocket broadcasting.
8. **If we wanted to deploy this to the cloud for multiple users and multiple server workers later, what would we change?**
   We would swap `InMemoryChannelLayer` for `channels_redis` (so multiple ASGI workers share WebSocket groups), switch SQLite to PostgreSQL, add token authentication with a `Device`/`User` foreign key on all models, and integrate FCM push notifications for background alerts.
