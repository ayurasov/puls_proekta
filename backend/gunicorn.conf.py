"""Настройки gunicorn в контейнере.

Рабочие процессы sync, как в todo-planner: запрос — процесс, перезапуск процесса по max_requests не теряет
запросов. У gthread при таком перезапуске принятые, но не начатые соединения обрывались (502 под нагрузкой,
см. docs/performance/loadtest-v0.2.0.md). Число процессов — GUNICORN_WORKERS (по умолчанию 2 × ядра + 1, не больше 9).
"""

import multiprocessing
import os

bind = "0.0.0.0:5000"
workers = int(os.environ.get("GUNICORN_WORKERS") or min(9, multiprocessing.cpu_count() * 2 + 1))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "sync")
threads = int(os.environ.get("GUNICORN_THREADS", "1"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "10000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "1000"))
accesslog = "-" if os.environ.get("ACCESS_LOG", "false").lower() == "true" else None
errorlog = "-"
capture_output = True
