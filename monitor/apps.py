"""
Django application configuration for the monitor app.
Exists to register the monitor app metadata with Django.
Called by Django when loading INSTALLED_APPS.
"""
from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "monitor"
