"""
Automated test suite covering REST endpoints, idempotency, summary math, and WebSockets.
Exists to verify all exam specification requirements with `python manage.py test`.
Called by Django's test runner.
"""
import uuid
from datetime import datetime, timezone as dt_timezone
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase

from posture_backend.asgi import application
from .models import AlertEvent, LatestStatus, MinuteAggregate


class PostureBackendTests(TestCase):
    """Tests for REST endpoints, idempotent upserts, and Python summary calculations."""

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


class WebSocketTests(TransactionTestCase):
    """Tests for WebSocket initial status push and live broadcasts from REST views."""

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

        async def run_ws_flow():
            communicator = WebsocketCommunicator(application, "/ws/notifications/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # 1. Immediately upon connect, the client receives the existing LatestStatus
            initial_msg = await communicator.receive_json_from()
            self.assertEqual(initial_msg["type"], "status")
            self.assertEqual(initial_msg["data"]["label"], "goodposture")

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

        from channels.db import database_sync_to_async

        @database_sync_to_async
        def database_post(path, data):
            return self.client.post(path, data, content_type="application/json")

        async_to_sync(run_ws_flow)()
