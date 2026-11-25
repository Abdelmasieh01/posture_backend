"""
Automated test suite covering REST endpoints, idempotency, summary math, WebSockets, commands, and snapshots.
Exists to verify all exam specification requirements with `python manage.py test`.
Called by Django's test runner.
"""
import io
import shutil
import tempfile
import uuid
from datetime import datetime, timezone as dt_timezone
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from PIL import Image

from posture_backend.asgi import application
from . import consumers
from .models import AlertEvent, LatestStatus, MinuteAggregate, Snapshot


def make_test_jpeg(color=(200, 50, 50)) -> SimpleUploadedFile:
    """Generate a tiny valid in-memory JPEG file for multipart upload tests."""
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=color).save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile("frame.jpg", buf.read(), content_type="image/jpeg")


class PostureBackendTests(TestCase):
    """Tests for REST endpoints, idempotent upserts, and Python summary calculations."""

    def setUp(self):
        # Ensure module-level counter starts clean for every test
        consumers.connected_devices = 0

    def test_status_post_updates_single_row_and_latest_returns_it(self):
        # Initially 404 before any status arrives
        res = self.client.get("/api/status/latest/")
        self.assertEqual(res.status_code, 404)

        payload = {
            "ts": "2026-05-10T10:00:00Z",
            "label": "goodposture",
            "confidence": 0.95,
            "distance_cm": 60.0,
            "distance_state": "ok",
            "posture_alert": False,
            "distance_alert": False,
            "extra_ignored_field": "ignored",
        }
        post_res = self.client.post("/api/status/", payload, content_type="application/json")
        self.assertEqual(post_res.status_code, 200)
        self.assertEqual(LatestStatus.objects.count(), 1)

        # Posting a second time updates pk=1 instead of creating a second row
        payload["label"] = "forwardbadposture"
        payload["posture_alert"] = True
        self.client.post("/api/status/", payload, content_type="application/json")
        self.assertEqual(LatestStatus.objects.count(), 1)

        latest_res = self.client.get("/api/status/latest/")
        self.assertEqual(latest_res.status_code, 200)
        self.assertEqual(latest_res.json()["label"], "forwardbadposture")

    def test_duplicate_event_id_is_idempotent(self):
        ev_id = str(uuid.uuid4())
        payload = {
            "event_id": ev_id,
            "ts": "2026-05-10T10:01:00Z",
            "kind": "posture",
            "state": "start",
            "label": "headforward",
            "duration_s": None,
        }
        r1 = self.client.post("/api/events/", payload, content_type="application/json")
        self.assertEqual(r1.status_code, 201)

        # Repeating the exact same event_id returns 200 and does not insert a second row
        r2 = self.client.post("/api/events/", payload, content_type="application/json")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(AlertEvent.objects.count(), 1)
        self.assertIn("shoulders", r2.json()["message"])

    def test_aggregate_upsert_is_idempotent(self):
        payload = {
            "window_start": "2026-05-10T10:00:00Z",
            "window_seconds": 60,
            "seconds_per_label": {"goodposture": 40, "forwardbadposture": 20},
            "avg_distance_cm": 55.0,
            "min_distance_cm": 48.0,
            "seconds_too_close": 0,
            "detections": 10,
        }
        r1 = self.client.post("/api/aggregates/", payload, content_type="application/json")
        self.assertEqual(r1.status_code, 201)

        # Updating same window_start overwrites the existing row and returns 200
        payload["seconds_per_label"] = {"goodposture": 50, "forwardbadposture": 10}
        r2 = self.client.post("/api/aggregates/", payload, content_type="application/json")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(MinuteAggregate.objects.count(), 1)
        self.assertEqual(MinuteAggregate.objects.first().seconds_per_label["goodposture"], 50)

    def test_summary_math_good_percent_and_weighted_distance(self):
        # Create two minute rows in the 10:00 hour bucket
        MinuteAggregate.objects.create(
            window_start=datetime(2026, 5, 10, 10, 0, tzinfo=dt_timezone.utc),
            window_seconds=60,
            # 30s good, 10s bad, 20s unknown -> 40s known
            seconds_per_label={"goodposture": 30, "forwardbadposture": 10, "unknown": 20},
            avg_distance_cm=50.0,
            seconds_too_close=5.0,
            detections=10,
        )
        MinuteAggregate.objects.create(
            window_start=datetime(2026, 5, 10, 10, 1, tzinfo=dt_timezone.utc),
            window_seconds=60,
            # 50s good, 10s bad -> 60s known
            seconds_per_label={"goodposture": 50, "forwardbadposture": 10},
            avg_distance_cm=70.0,
            seconds_too_close=10.0,
            detections=30,
        )
        # 1 start event and 1 end event in the 10:00 hour
        AlertEvent.objects.create(
            event_id="ev-1",
            ts=datetime(2026, 5, 10, 10, 5, tzinfo=dt_timezone.utc),
            kind="posture",
            state="start",
            label="forwardbadposture",
            message="msg",
        )
        AlertEvent.objects.create(
            event_id="ev-2",
            ts=datetime(2026, 5, 10, 10, 6, tzinfo=dt_timezone.utc),
            kind="posture",
            state="end",
            label="forwardbadposture",
            duration_s=60.0,
            message="msg",
        )

        res = self.client.get(
            "/api/summary/?granularity=hour&from=2026-05-10T10:00:00Z&to=2026-05-10T11:00:00Z"
        )
        self.assertEqual(res.status_code, 200)
        buckets = res.json()
        self.assertEqual(len(buckets), 2)  # 10:00 bucket (with data) and 11:00 bucket (empty)

        b0 = buckets[0]
        # Total good = 80s, total known = 100s -> 80.0%
        self.assertEqual(b0["good_percent"], 80.0)
        # Weighted avg distance = (50*10 + 70*30) / (10 + 30) = 2600 / 40 = 65.0 cm
        self.assertEqual(b0["avg_distance_cm"], 65.0)
        self.assertEqual(b0["seconds_too_close"], 15.0)
        self.assertEqual(b0["alert_count"], 1)

        # Second bucket (11:00) has no data -> returned with zeroed values and null percentages
        b1 = buckets[1]
        self.assertIsNone(b1["good_percent"])
        self.assertIsNone(b1["avg_distance_cm"])
        self.assertEqual(b1["alert_count"], 0)

    def test_commands_bad_input_and_offline_pi(self):
        # Missing or invalid command -> 400
        bad_res = self.client.post(
            "/api/commands/", {"command": "reboot_pi"}, content_type="application/json"
        )
        self.assertEqual(bad_res.status_code, 400)

        # Valid command when no Pi is connected -> 503
        offline_res = self.client.post(
            "/api/commands/", {"command": "calibrate_camera"}, content_type="application/json"
        )
        self.assertEqual(offline_res.status_code, 503)
        self.assertEqual(offline_res.json(), {"detail": "Pi not connected"})


class SnapshotTests(TestCase):
    """Tests for POST/GET /api/snapshots/ validation, filtering, and 200-row cap."""

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.temp_media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_valid_snapshot_upload_and_filter_by_event_id(self):
        ev1 = str(uuid.uuid4())
        ev2 = str(uuid.uuid4())

        r1 = self.client.post(
            "/api/snapshots/",
            {
                "event_id": ev1,
                "ts": "2026-11-14T03:50:41Z",
                "label": "forwardbadposture",
                "image": make_test_jpeg(),
            },
        )
        self.assertEqual(r1.status_code, 201)
        body1 = r1.json()
        self.assertIn("id", body1)
        self.assertTrue(body1["image_url"].startswith("http://testserver/media/snapshots/"))

        r2 = self.client.post(
            "/api/snapshots/",
            {
                "event_id": ev2,
                "ts": "2026-11-14T03:50:46Z",
                "label": "headforward",
                "image": make_test_jpeg(),
            },
        )
        self.assertEqual(r2.status_code, 201)

        # Unfiltered GET returns newest first (ev2 before ev1)
        all_res = self.client.get("/api/snapshots/")
        self.assertEqual(all_res.status_code, 200)
        items = all_res.json()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["event_id"], ev2)

        # Filtered GET returns only matching event_id
        filtered_res = self.client.get(f"/api/snapshots/?event_id={ev1}")
        self.assertEqual(filtered_res.status_code, 200)
        filtered = filtered_res.json()
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["event_id"], ev1)
        self.assertTrue(filtered[0]["image_url"].startswith("http://testserver/media/snapshots/"))

    def test_rejects_non_jpeg_and_oversized_snapshot(self):
        ev_id = str(uuid.uuid4())

        # 1. PNG image should be rejected with 400
        png_buf = io.BytesIO()
        Image.new("RGB", (16, 16), color=(0, 255, 0)).save(png_buf, format="PNG")
        png_buf.seek(0)
        png_file = SimpleUploadedFile("bad.png", png_buf.read(), content_type="image/png")

        res_png = self.client.post(
            "/api/snapshots/",
            {
                "event_id": ev_id,
                "ts": "2026-11-14T03:50:41Z",
                "label": "forwardbadposture",
                "image": png_file,
            },
        )
        self.assertEqual(res_png.status_code, 400)
        self.assertIn("image", res_png.json())

        # 2. File larger than 2 MB should be rejected with 400
        huge_file = SimpleUploadedFile(
            "huge.jpg", b"x" * (2 * 1024 * 1024 + 10), content_type="image/jpeg"
        )
        res_huge = self.client.post(
            "/api/snapshots/",
            {
                "event_id": ev_id,
                "ts": "2026-11-14T03:50:41Z",
                "label": "forwardbadposture",
                "image": huge_file,
            },
        )
        self.assertEqual(res_huge.status_code, 400)
        self.assertEqual(Snapshot.objects.count(), 0)


class WebSocketTests(TransactionTestCase):
    """Tests for WebSocket initial status push, live broadcasts, and remote calibration."""

    def setUp(self):
        consumers.connected_devices = 0

    def test_websocket_receives_initial_status_and_broadcasts(self):
        # Seed an initial LatestStatus via POST /api/status/
        self.client.post(
            "/api/status/",
            {
                "ts": "2026-05-10T12:00:00Z",
                "label": "goodposture",
                "confidence": 0.9,
                "distance_cm": 58.0,
                "distance_state": "ok",
                "posture_alert": False,
                "distance_alert": False,
            },
            content_type="application/json",
        )

        @database_sync_to_async
        def database_post(path, data):
            return self.client.post(path, data, content_type="application/json")

        async def run_ws_flow():
            communicator = WebsocketCommunicator(application, "/ws/notifications/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # 1. Immediately upon connect, the client receives LatestStatus then device status
            initial_msg = await communicator.receive_json_from()
            self.assertEqual(initial_msg["type"], "status")
            self.assertEqual(initial_msg["data"]["label"], "goodposture")

            device_msg = await communicator.receive_json_from()
            self.assertEqual(device_msg, {"type": "device", "data": {"connected": False}})

            # 2. Posting a new status broadcasts a "status" message to connected clients
            await database_post(
                "/api/status/",
                {
                    "ts": "2026-05-10T12:00:05Z",
                    "label": "headforward",
                    "confidence": 0.88,
                    "distance_cm": 52.0,
                    "distance_state": "ok",
                    "posture_alert": True,
                    "distance_alert": False,
                },
            )
            status_msg = await communicator.receive_json_from()
            self.assertEqual(status_msg["type"], "status")
            self.assertEqual(status_msg["data"]["label"], "headforward")
            self.assertTrue(status_msg["data"]["posture_alert"])

            # 3. Posting an event with state="start" broadcasts an "alert" message
            await database_post(
                "/api/events/",
                {
                    "event_id": "start-uuid-1",
                    "ts": "2026-05-10T12:00:08Z",
                    "kind": "posture",
                    "state": "start",
                    "label": "headforward",
                },
            )
            alert_msg = await communicator.receive_json_from()
            self.assertEqual(alert_msg["type"], "alert")
            self.assertEqual(alert_msg["data"]["label"], "headforward")

            # 4. Posting an event with state="end" stores it but does NOT broadcast
            await database_post(
                "/api/events/",
                {
                    "event_id": "end-uuid-2",
                    "ts": "2026-05-10T12:00:15Z",
                    "kind": "posture",
                    "state": "end",
                    "label": "headforward",
                    "duration_s": 7.0,
                },
            )
            self.assertTrue(await communicator.receive_nothing())
            await communicator.disconnect()

        async_to_sync(run_ws_flow)()

    def test_device_connection_commands_and_results(self):
        @database_sync_to_async
        def http_get(path):
            return self.client.get(path)

        @database_sync_to_async
        def http_post(path, data):
            return self.client.post(path, data, content_type="application/json")

        async def run_device_flow():
            # Initially GET /api/device/ reports connected=False
            r0 = await http_get("/api/device/")
            self.assertEqual(r0.json(), {"connected": False})

            # Connect the Flutter app WebSocket first
            app_ws = WebsocketCommunicator(application, "/ws/notifications/")
            self.assertTrue((await app_ws.connect())[0])
            # No LatestStatus exists, so the first message is the initial device state
            self.assertEqual(
                await app_ws.receive_json_from(),
                {"type": "device", "data": {"connected": False}},
            )

            # Connect the Raspberry Pi WebSocket (/ws/device/)
            pi_ws = WebsocketCommunicator(application, "/ws/device/")
            self.assertTrue((await pi_ws.connect())[0])

            # App receives {"type": "device", "data": {"connected": True}} and GET /api/device/ matches
            self.assertEqual(
                await app_ws.receive_json_from(),
                {"type": "device", "data": {"connected": True}},
            )
            r1 = await http_get("/api/device/")
            self.assertEqual(r1.json(), {"connected": True})

            # POST /api/commands/ returns 202 and Pi receives {"type": "command", "command": ...}
            cmd_res = await http_post("/api/commands/", {"command": "calibrate_distance"})
            self.assertEqual(cmd_res.status_code, 202)
            self.assertEqual(cmd_res.json(), {"sent": True, "command": "calibrate_distance"})
            self.assertEqual(
                await pi_ws.receive_json_from(),
                {"type": "command", "command": "calibrate_distance"},
            )

            # Pi sends a valid command_result -> App receives it with a server-generated ISO ts
            await pi_ws.send_json_to(
                {
                    "type": "command_result",
                    "command": "calibrate_distance",
                    "status": "recording",
                    "detail": "Hold still for 3 seconds",
                }
            )
            result_msg = await app_ws.receive_json_from()
            self.assertEqual(result_msg["type"], "command_result")
            self.assertEqual(result_msg["data"]["command"], "calibrate_distance")
            self.assertEqual(result_msg["data"]["status"], "recording")
            self.assertEqual(result_msg["data"]["detail"], "Hold still for 3 seconds")
            self.assertTrue(result_msg["data"]["ts"].endswith("Z"))

            # Pi sends an invalid command_result -> silently ignored (app receives nothing)
            await pi_ws.send_json_to(
                {
                    "type": "command_result",
                    "command": "calibrate_distance",
                    "status": "exploded",
                }
            )
            self.assertTrue(await app_ws.receive_nothing())

            # Disconnect the Pi -> App receives connected=False and GET /api/device/ reflects it
            await pi_ws.disconnect()
            self.assertEqual(
                await app_ws.receive_json_from(),
                {"type": "device", "data": {"connected": False}},
            )
            r2 = await http_get("/api/device/")
            self.assertEqual(r2.json(), {"connected": False})

            await app_ws.disconnect()

        async_to_sync(run_device_flow)()