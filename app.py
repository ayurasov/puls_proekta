#!/usr/bin/env python3
"""
Пульс Проекта — Python WSGI-обёртка для запуска через gunicorn.

Запускает Node.js сервер как подпроцесс и проксирует все запросы к нему.
Использование:
    gunicorn app:app --bind 0.0.0.0:39663
"""

import os
import sys
import subprocess
import time
import signal
import atexit
import threading

# Устанавливаем порт для Node.js (внутренний)
NODE_PORT = os.environ.get("NODE_PORT", "5500")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NODE_SCRIPT = os.path.join(BASE_DIR, "dist", "index.cjs")

_node_proc = None


def _start_node():
    """Запуск Node.js сервера как подпроцесса."""
    global _node_proc
    env = dict(os.environ)
    env["NODE_ENV"] = "production"
    env["PORT"] = NODE_PORT
    _node_proc = subprocess.Popen(
        [sys.executable.replace("python3", "node"), NODE_SCRIPT]
        if "python" in sys.executable
        else ["node", NODE_SCRIPT],
        cwd=BASE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Ждём запуска
    time.sleep(3)
    if _node_proc.poll() is not None:
        # Процесс упал — читаем вывод
        out = _node_proc.stdout.read().decode() if _node_proc.stdout else ""
        raise RuntimeError(f"Node.js server failed to start:\n{out}")


def _stop_node():
    """Остановка Node.js сервера."""
    global _node_proc
    if _node_proc and _node_proc.poll() is None:
        _node_proc.terminate()
        try:
            _node_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _node_proc.kill()


atexit.register(_stop_node)


# --- WSGI-приложение (без Flask, чистый stdlib) ---

import http.client
from urllib.parse import urlparse, urlunparse


def _proxy_wsgi(environ, start_response):
    """Проксирование WSGI-запроса к Node.js серверу."""
    host = "127.0.0.1"
    port = int(NODE_PORT)

    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    query = environ.get("QUERY_STRING", "")
    uri = path + ("?" + query if query else "")

    # Заголовки
    headers = {}
    for key in environ:
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            headers[name] = environ[key]
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    if environ.get("CONTENT_LENGTH"):
        headers["Content-Length"] = environ["CONTENT_LENGTH"]
    headers["Host"] = f"{host}:{port}"
    headers["X-Real-IP"] = environ.get("REMOTE_ADDR", "")
    headers["X-Forwarded-For"] = environ.get("HTTP_X_FORWARDED_FOR", environ.get("REMOTE_ADDR", ""))
    headers["X-Forwarded-Proto"] = environ.get("HTTP_X_FORWARDED_PROTO", "http")

    # Тело запроса
    body = b""
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length:
            body = environ["wsgi.input"].read(length)
    except (ValueError, KeyError):
        pass

    # Запрос к Node.js
    try:
        conn = http.client.HTTPConnection(host, port, timeout=30)
        conn.request(method, uri, body=body, headers=headers)
        resp = conn.getresponse()
        resp_body = resp.read()
        conn.close()
    except (ConnectionRefusedError, OSError):
        start_response("502 Bad Gateway", [("Content-Type", "text/plain")])
        return [b"Node.js server is not responding"]

    # Ответ
    resp_headers = []
    for k, v in resp.getheaders():
        if k.lower() in ("transfer-encoding", "connection"):
            continue
        resp_headers.append((k, v))

    start_response(f"{resp.status} {resp.reason}", resp_headers)
    return [resp_body]


# Объект приложения для gunicorn
class PulsApp:
    """WSGI-приложение: запускает Node.js при первом запросе и проксирует."""

    _started = False
    _lock = threading.Lock()

    def _ensure_started(self):
        if not self._started:
            with self._lock:
                if not self._started:
                    _start_node()
                    self._started = True

    def __call__(self, environ, start_response):
        self._ensure_started()
        return _proxy_wsgi(environ, start_response)


app = PulsApp()
