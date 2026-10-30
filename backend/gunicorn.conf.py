"""Настройки gunicorn в контейнере."""

import os

bind = "0.0.0.0:5000"
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5
max_requests = 5000
max_requests_jitter = 500
accesslog = "-" if os.environ.get("ACCESS_LOG", "false").lower() == "true" else None
errorlog = "-"
capture_output = True
