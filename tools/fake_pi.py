#!/usr/bin/env python
"""
Standalone Raspberry Pi simulator script using `requests`.
Exists to test live status updates, alert start/end transitions, and minute aggregates.
Called from the terminal via `python tools/fake_pi.py --base http://localhost:8000 --fast`.
"""
import argparse
import time
import uuid
from datetime import datetime, timedelta, timezone
import requests


def iso_now(dt: datetime | None = None) -> str:
    """Return a UTC timestamp formatted as ISO 8601 ending in Z."""
    target = dt or datetime.now(timezone.utc)
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    parser = argparse.ArgumentParser(description="Simulate the Raspberry Pi posture monitor.")
    parser.add_argument("--base", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Post a minute aggregate every 10s (instead of 60s) with consecutive window_start.",
    )
    args = parser.parse_args()
    base = args.base.rstrip("/")
    agg_interval = 10.0 if args.fast else 60.0

    print(f"Starting fake Pi -> {base} (aggregate interval: {agg_interval}s)")

    # State machine variables to simulate alternating good and bad posture phases
    bad_labels = ["forwardbadposture", "headforward", "backwardbadposture"]
    bad_idx = 0
    in_bad_posture = False
    phase_start = time.monotonic()
    active_alert_label = None
    active_alert_start = None

    last_status_ts = 0.0
    last_agg_ts = time.monotonic()
    # Track consecutive 1-minute windows for aggregate posts
    next_window_start = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    while True:
        now_mono = time.monotonic()
        elapsed_in_phase = now_mono - phase_start

        # Switch between 15s of good posture and 15s of bad posture
        if elapsed_in_phase >= 15.0:
            if not in_bad_posture:
                in_bad_posture = True
                active_alert_label = bad_labels[bad_idx % len(bad_labels)]
                bad_idx += 1
                phase_start = now_mono
                elapsed_in_phase = 0.0
            else:
                # Recovering from bad posture -> if an alert was started, send the "end" event
                if active_alert_start is not None:
                    duration = round(now_mono - active_alert_start, 1)
                    end_event = {
                        "event_id": str(uuid.uuid4()),
                        "ts": iso_now(),
                        "kind": "posture",
                        "state": "end",
                        "label": active_alert_label,
                        "duration_s": duration,
                    }
                    r = requests.post(f"{base}/api/events/", json=end_event, timeout=5)
                    print(f"[EVENT END]   {active_alert_label} ({duration}s) -> {r.status_code}")
                    active_alert_start = None

                in_bad_posture = False
                phase_start = now_mono
                elapsed_in_phase = 0.0

        # When bad posture has lasted 8 seconds, trigger an alert "start" event once
        if in_bad_posture and elapsed_in_phase >= 8.0 and active_alert_start is None:
            active_alert_start = now_mono
            start_event = {
                "event_id": str(uuid.uuid4()),
                "ts": iso_now(),
                "kind": "posture",
                "state": "start",
                "label": active_alert_label,
                "duration_s": None,
            }
            r = requests.post(f"{base}/api/events/", json=start_event, timeout=5)
            print(f"[EVENT START] {active_alert_label} -> {r.status_code}")

        # Every 5 seconds, post the current live status
        if now_mono - last_status_ts >= 5.0:
            last_status_ts = now_mono
            label = active_alert_label if in_bad_posture else "goodposture"
            status_payload = {
                "ts": iso_now(),
                "label": label,
                "confidence": 0.92,
                "distance_cm": 54.5 if not in_bad_posture else 46.0,
                "distance_state": "ok",
                "posture_alert": active_alert_start is not None,
                "distance_alert": False,
            }
            r = requests.post(f"{base}/api/status/", json=status_payload, timeout=5)
            print(
                f"[STATUS]      label={label} alert={status_payload['posture_alert']} -> {r.status_code}"
            )

        # Post a MinuteAggregate every `agg_interval` seconds with consecutive window_start
        if now_mono - last_agg_ts >= agg_interval:
            last_agg_ts = now_mono
            agg_payload = {
                "window_start": iso_now(next_window_start),
                "window_seconds": 60,
                "seconds_per_label": {
                    "goodposture": 42,
                    "forwardbadposture": 13,
                    "unknown": 5,
                },
                "avg_distance_cm": 55.2,
                "min_distance_cm": 44.0,
                "seconds_too_close": 0.0,
                "detections": 12,
                "avg_angles": {
                    "neck_angle": 21.3,
                    "torso_angle": 3.1,
                    "alignment_angle": 171.2,
                    "head_offset": 0.08,
                    "head_pitch": 12.5,
                },
            }
            r = requests.post(f"{base}/api/aggregates/", json=agg_payload, timeout=5)
            print(f"[AGGREGATE]   window_start={agg_payload['window_start']} -> {r.status_code}")
            next_window_start += timedelta(minutes=1)

        time.sleep(0.5)


if __name__ == "__main__":
    main()
