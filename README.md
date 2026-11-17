# Posture Monitor Demo Backend (`posture_backend`)

A lightweight, single-process Django 5 + Django REST Framework + Django Channels backend for a Raspberry Pi posture and screen-distance monitoring school demo, including remote calibration commands.

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
| `GET` | `/api/summary/` | App | Computes hourly or daily chart buckets in Python (`?granularity=hour\|day&from=&to=`). |
| `POST` | `/api/commands/` | App | Sends a calibration command (`calibrate_camera` or `calibrate_distance`) to the connected Pi (`202` if sent, `503` if Pi is offline). |
| `GET` | `/api/device/` | App | Returns `{"connected": true\|false}` based on active `/ws/device/` connections. |

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

**Check whether the Pi is connected (from App):**
```bash
curl -s http://localhost:8000/api/device/
```

**Trigger remote calibration (from App):**
```bash
curl -i -X POST http://localhost:8000/api/commands/ \
  -H "Content-Type: application/json" \
  -d '{"command": "calibrate_camera"}'
```

---

## 3. WebSockets & Message Formats

### A. App WebSocket: `ws://<host>:8000/ws/notifications/`
Push-only stream for the Flutter app. On connect, it immediately sends `LatestStatus` (if any) followed by the current Pi connection state.

* **Live status:** `{"type": "status", "data": {"ts", "label", "confidence", "distance_cm", "distance_state", "posture_alert", "distance_alert"}}`
* **Live alert:** `{"type": "alert", "data": {"kind", "label", "message", "ts"}}`
* **Pi connection state:** `{"type": "device", "data": {"connected": true}}`
* **Calibration progress:** `{"type": "command_result", "data": {"command": "calibrate_camera", "status": "recording", "detail": "Hold upright", "ts": "2026-05-10T14:05:00Z"}}`

### B. Pi Agent WebSocket: `ws://<host>:8000/ws/device/`
Bidirectional channel for the Raspberry Pi agent.

* **Backend -> Pi (command):**
  `{"type": "command", "command": "calibrate_camera"}` (or `"calibrate_distance"`)
* **Pi -> Backend (progress):**
  `{"type": "command_result", "command": "calibrate_camera", "status": "get_ready" | "recording" | "done" | "failed", "detail": "optional text"}`

### Testing WebSockets

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

Or with `websocat`:
```bash
websocat ws://localhost:8000/ws/notifications/
websocat ws://localhost:8000/ws/device/
```

---

## 4. End-to-End Data Flow

1. **Telemetry (Pi -> App):** The Pi posts posture status, alert events, and minute aggregates over HTTP to Daphne on port 8000.
2. **Persistence & Broadcast:** DRF views validate payloads, persist them to SQLite, and call `async_to_sync(channel_layer.group_send)` to push `"status"` and `"alert"` frames to `/ws/notifications/`.
3. **Pi Presence:** When the Pi connects to `/ws/device/`, `DeviceConsumer` increments `connected_devices` and broadcasts `{"type": "device", "data": {"connected": true}}` to the app (and `false` on disconnect).
4. **Remote Calibration Command:** The Flutter app sends `POST /api/commands/` with `{"command": "calibrate_camera"}`.
5. **Command Dispatch:** If `connected_devices > 0`, Django pushes `{"type": "command", "command": ...}` to the `"device"` group over `/ws/device/` and returns `202 Accepted`.
6. **Calibration Progress:** As the Pi runs calibration, it sends `"command_result"` JSON frames (`get_ready`, `recording`, `done`, `failed`) over `/ws/device/`, which Django validates, timestamps, and forwards to `/ws/notifications/`.

---

## 5. Design Decisions and Why

- **No authentication or permissions:** Single Pi, single user, trusted local Wi-Fi router; avoids login friction during the live demo.
- **In-memory channel layer (`InMemoryChannelLayer`):** Stores WebSocket groups in process RAM, avoiding a separate Redis server.
- **Per-minute aggregates (`MinuteAggregate`):** Keeps SQLite tiny (1,440 rows/day) and chart queries fast.
- **Idempotent upserts (`event_id` and `window_start`):** Retries after brief Wi-Fi drops never create duplicate alerts or double-counted minutes.
- **Status not stored historically (`LatestStatus` `pk=1`):** Overwriting one row avoids 17,280 status rows/day while giving new WebSocket clients an instant snapshot.
- **Commands not stored or queued:** Calibration requires the user to be sitting in front of the camera right now; replaying a stale queued command later would calibrate against an empty chair or bad posture.
- **`503 Service Unavailable` when Pi is offline:** Gives the Flutter app an immediate, explicit error instead of silently accepting a command that cannot be executed.
- **In-memory `connected_devices` counter:** A single module-level integer is the simplest way to track live Pi presence without database writes on every connect/disconnect.

---

## 6. Limitations

- **Notifications only reach an open app:** No FCM push notifications; the phone must have the app open on the LAN.
- **Single-process only:** Both `InMemoryChannelLayer` and the `connected_devices` counter live in Python process RAM, so multiple worker processes would not share state.
- **Command lost if Pi is offline:** Because commands are not queued, any command sent while the Pi is disconnected returns `503` and must be retried by the user once the Pi reconnects.
- **No authentication / plain HTTP & WS:** Any device on the same LAN can call the API or connect to either WebSocket.

---

## 7. Assumptions

- All timestamps sent by the Pi or passed in query parameters (`since`, `from`, `to`) are valid ISO 8601 strings (naive timestamps default to UTC).
- `GET /api/summary/` floors `from` and `to` down to clock-aligned hour or day boundaries.
- Unknown extra JSON fields in `POST` bodies are ignored by DRF serializers.
- Only one Raspberry Pi connects to `/ws/device/` in normal demo use, though `connected_devices` safely counts multiple connections if a test script connects alongside the Pi.

---

## 8. Likely Examiner Questions & Answers

1. **Why must `"daphne"` be placed at the very top of `INSTALLED_APPS`?**
   So Daphne overrides Django's default WSGI-only `runserver` with an ASGI-capable server that supports both HTTP and WebSockets.
2. **Why do we call `get_asgi_application()` in `asgi.py` before importing `monitor.routing`?**
   `monitor.routing` imports consumers that import Django models, which requires Django's app registry to be initialized first.
3. **How can a synchronous DRF view broadcast to an asynchronous WebSocket consumer?**
   `asgiref.sync.async_to_sync(channel_layer.group_send)` submits the async coroutine to the ASGI event loop and waits briefly for it to queue the message.
4. **Why don't we broadcast a WebSocket `"alert"` message on `"end"` events?**
   The app clears its alert banner when a `"status"` message arrives with `posture_alert: false` and `distance_alert: false`; `"end"` is stored only for historical duration logs.
5. **How does `GET /api/summary/` calculate `good_percent`?**
   It sums all `seconds_per_label` values except `"unknown"`, then divides `goodposture` seconds by that known total (returning `null` if the total is 0).
6. **Why is `avg_distance_cm` weighted by `detections`?**
   So a minute with 30 camera detections carries proportionally more weight than a minute with only 2 detections.
7. **What happens if the Pi retries the same `POST /api/events/` request?**
   The backend looks up `event_id`; if it already exists, it returns `200 OK` without inserting a second row or re-broadcasting.
8. **What would change for a multi-user cloud deployment?**
   Replace `InMemoryChannelLayer` with `channels_redis`, SQLite with PostgreSQL, add token auth + device foreign keys, and add FCM push notifications.
9. **Why does `POST /api/commands/` return `202 Accepted` instead of `200 OK`?**
   `202 Accepted` accurately signals that the backend has forwarded the command to the Pi, but calibration itself happens asynchronously on the Pi and reports progress later over WebSocket.
10. **Why don't we store calibration commands in SQLite and deliver them when the Pi reconnects?**
    Calibration requires the user to be actively sitting upright in front of the camera at that moment; executing a delayed command later would produce invalid calibration data.
11. **Why is `connected_devices` a simple Python integer instead of a database column?**
    In a single-process ASGI server, an in-memory counter is instant and always reflects live sockets—whereas a database flag can get stuck as `True` if the server crashes or restarts.
12. **Why does `DeviceConsumer.receive_json` validate incoming Pi messages with a DRF serializer?**
    Validating with `CommandResultSerializer` guarantees that only expected `command` and `status` values reach the Flutter app, preventing malformed Pi messages from crashing the mobile UI.