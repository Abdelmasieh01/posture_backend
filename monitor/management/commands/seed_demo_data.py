"""
Management command `seed_demo_data` to populate SQLite with realistic demo rows.
Exists so the Flutter dashboard charts and alert list can be demonstrated without a live Pi.
Called manually by running `python manage.py seed_demo_data --hours 24`.
"""
import random
import uuid
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import AlertEvent, LatestStatus, MinuteAggregate
from monitor.notifications import get_alert_message


class Command(BaseCommand):
    help = "Seeds fake MinuteAggregate, AlertEvent, and LatestStatus rows for demos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Number of past hours of minute aggregates to generate (default: 24).",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        total_minutes = hours * 60
        now = timezone.now().replace(second=0, microsecond=0)
        start_time = now - timedelta(minutes=total_minutes)

        rng = random.Random(42)  # Fixed seed so demo charts look consistent and reproducible
        agg_count = 0
        event_count = 0

        for i in range(total_minutes):
            win_start = start_time + timedelta(minutes=i)
            good_s = float(rng.randint(25, 55))
            bad_s = float(rng.randint(0, int(60 - good_s)))
            unknown_s = float(60.0 - good_s - bad_s)
            bad_label = rng.choice(["forwardbadposture", "backwardbadposture", "headforward"])
            too_close_s = float(rng.choice([0, 0, 0, 5, 12]))
            avg_dist = round(rng.uniform(42.0, 68.0), 1)

            MinuteAggregate.objects.update_or_create(
                window_start=win_start,
                defaults={
                    "window_seconds": 60,
                    "seconds_per_label": {
                        "goodposture": good_s,
                        bad_label: bad_s,
                        "unknown": unknown_s,
                    },
                    "avg_distance_cm": avg_dist,
                    "min_distance_cm": round(avg_dist - rng.uniform(3.0, 10.0), 1),
                    "seconds_too_close": too_close_s,
                    "detections": rng.randint(10, 30),
                    "avg_angles": {
                        "neck_angle": round(rng.uniform(15.0, 28.0), 1),
                        "torso_angle": round(rng.uniform(2.0, 8.0), 1),
                        "alignment_angle": round(rng.uniform(165.0, 178.0), 1),
                        "head_offset": round(rng.uniform(0.03, 0.14), 2),
                        "head_pitch": round(rng.uniform(8.0, 18.0), 1),
                    },
                },
            )
            agg_count += 1

            # Create an AlertEvent pair roughly every 90 minutes
            if i % 90 == 30:
                kind = rng.choice(["posture", "distance"])
                label = "tooclose" if kind == "distance" else bad_label
                msg = get_alert_message(kind, label)
                AlertEvent.objects.create(
                    event_id=str(uuid.uuid4()),
                    ts=win_start + timedelta(seconds=10),
                    kind=kind,
                    state="start",
                    label=label,
                    duration_s=None,
                    message=msg,
                )
                AlertEvent.objects.create(
                    event_id=str(uuid.uuid4()),
                    ts=win_start + timedelta(seconds=35),
                    kind=kind,
                    state="end",
                    label=label,
                    duration_s=25.0,
                    message=msg,
                )
                event_count += 2

        LatestStatus.objects.update_or_create(
            pk=1,
            defaults={
                "ts": now,
                "label": "goodposture",
                "confidence": 0.94,
                "distance_cm": 58.5,
                "distance_state": "ok",
                "posture_alert": False,
                "distance_alert": False,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {agg_count} MinuteAggregate rows, {event_count} AlertEvent rows, and LatestStatus."
            )
        )
