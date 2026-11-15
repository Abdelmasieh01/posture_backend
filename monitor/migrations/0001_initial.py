"""
Initial database migration creating LatestStatus, AlertEvent, and MinuteAggregate.
Exists so `python manage.py migrate` works immediately out of the box.
Called by Django's migration executor.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AlertEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=64, unique=True)),
                ("ts", models.DateTimeField(db_index=True)),
                ("kind", models.CharField(max_length=32)),
                ("state", models.CharField(max_length=16)),
                ("label", models.CharField(max_length=64)),
                ("duration_s", models.FloatField(blank=True, null=True)),
                ("message", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="LatestStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ts", models.DateTimeField()),
                ("label", models.CharField(max_length=64)),
                ("confidence", models.FloatField(blank=True, null=True)),
                ("distance_cm", models.FloatField(blank=True, null=True)),
                ("distance_state", models.CharField(max_length=32)),
                ("posture_alert", models.BooleanField()),
                ("distance_alert", models.BooleanField()),
                ("received_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="MinuteAggregate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("window_start", models.DateTimeField(db_index=True, unique=True)),
                ("window_seconds", models.IntegerField(default=60)),
                ("seconds_per_label", models.JSONField()),
                ("avg_distance_cm", models.FloatField(blank=True, null=True)),
                ("min_distance_cm", models.FloatField(blank=True, null=True)),
                ("seconds_too_close", models.FloatField(default=0.0)),
                ("detections", models.IntegerField()),
                ("avg_angles", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
