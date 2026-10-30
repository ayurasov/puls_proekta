"""Нагрузочная проверка «Пульса Проекта»: опрос изменений, открытие плана, список проектов, правки.

    python3 tools/perf/loadtest.py http://127.0.0.1:39663 --login admin --password … [--seconds 20]

Запускается против тестового стенда (Docker Compose с перенесённой базой), не против production:
сценарий правок создаёт свой проект «Нагрузочный тест» на 150 задач и удаляет его в конце (в корзину).
Каждый поток держит своё keep-alive соединение и общую сессию. Результат — таблица Markdown:
запросов в секунду, задержки p50/p95/p99 и ошибки для каждого сценария и числа потоков.
"""

import argparse
import http.client
import json
import random
import statistics
import threading
import time
from urllib.parse import urlparse


class Session:
    def __init__(self, base, login, password):
        url = urlparse(base)
        self.host, self.port = url.hostname, url.port or 80
        self.cookie = ""
        self.csrf = ""
        self.request("GET", "/api/auth/csrf")
        self.csrf = self.last["csrfToken"]
        status = self.request("POST", "/api/auth/login", {"login": login, "password": password})
        if status != 200:
            raise SystemExit(f"Вход не удался: {status} {self.last}")
        self.request("GET", "/api/auth/csrf")
        self.csrf = self.last["csrfToken"]

    def connection(self):
        return http.client.HTTPConnection(self.host, self.port, timeout=30)

    def request(self, method, path, body=None, conn=None):
        own = conn is None
        conn = conn or self.connection()
        headers = {"Content-Type": "application/json", "Cookie": self.cookie, "X-CSRF-Token": self.csrf}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = res.read()
        for name, value in res.getheaders():
            if name.lower() == "set-cookie":
                self.cookie = "; ".join(filter(None, [c for c in self.cookie.split("; ") if not c.startswith(value.split("=")[0] + "=")] + [value.split(";")[0]]))
        try:
            self.last = json.loads(data) if data else None
        except ValueError:
            self.last = None
        if own:
            conn.close()
        return res.status


def run(session, threads, seconds, make_request):
    latencies, errors, codes = [], [0], {}
    lock = threading.Lock()
    stop = time.monotonic() + seconds

    def worker():
        conn = session.connection()
        local, bad = [], 0
        while time.monotonic() < stop:
            method, path, body = make_request()
            started = time.perf_counter()
            try:
                status = session_request(conn, method, path, body)
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                # nginx закрывает keep-alive соединение после keepalive_requests — повторяем по новому
                conn.close()
                conn = session.connection()
                started = time.perf_counter()
                try:
                    status = session_request(conn, method, path, body)
                except (OSError, http.client.HTTPException):
                    status = 0
            except (OSError, http.client.HTTPException):
                conn.close()
                conn = session.connection()
                status = 0
            local.append(time.perf_counter() - started)
            if status >= 400 or status == 0:
                bad += 1
                with lock:
                    codes[status] = codes.get(status, 0) + 1
        conn.close()
        with lock:
            latencies.extend(local)
            errors[0] += bad

    def session_request(conn, method, path, body):
        headers = {"Content-Type": "application/json", "Cookie": session.cookie, "X-CSRF-Token": session.csrf}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = conn.getresponse()
        res.read()
        return res.status

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    latencies.sort()

    def pct(p):
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))] * 1000 if latencies else 0

    return {"requests": len(latencies), "rps": len(latencies) / seconds, "p50": pct(0.50), "p95": pct(0.95),
            "p99": pct(0.99), "mean": statistics.mean(latencies) * 1000 if latencies else 0, "errors": errors[0],
            "codes": codes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--login", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--plan", type=int, help="план для чтения (по умолчанию — самый большой)")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--threads", default="10,40")
    args = parser.parse_args()
    session = Session(args.base, args.login, args.password)

    if args.plan is None:
        session.request("GET", "/api/projects")
        biggest = None
        for project in session.last:
            for plan in project.get("plans") or []:
                session.request("GET", f"/api/plans/{plan['id']}")
                size = len(session.last.get("items") or [])
                if biggest is None or size > biggest[1]:
                    biggest = (plan["id"], size)
        args.plan = biggest[0]
    session.request("GET", f"/api/plans/{args.plan}")
    revision = session.last["plan"]["revision"]
    size = len(session.last["items"])

    # Свой проект для правок: 150 задач, удаляется в конце
    session.request("POST", "/api/projects", {"title": "Нагрузочный тест"})
    test_project = session.last["id"]
    test_plan = session.last["plans"][0]["id"]
    ops = [{"op": "item.create", "item": {"key": f"t{i}", "type": "task", "name": f"Задача {i}",
                                          "start": "2026-11-02", "end": "2026-11-20"}} for i in range(150)]
    session.request("POST", f"/api/plans/{test_plan}/ops", {"ops": ops})

    scenarios = [
        ("Опрос изменений (без изменений)", lambda: ("GET", f"/api/plans/{args.plan}/changes?since={revision}", None)),
        (f"Открытие плана ({size} строк)", lambda: ("GET", f"/api/plans/{args.plan}", None)),
        ("Список проектов", lambda: ("GET", "/api/projects", None)),
        ("Правка строки (один план, все потоки)", lambda: ("POST", f"/api/plans/{test_plan}/ops", {
            "ops": [{"op": "item.update", "key": f"t{random.randrange(150)}",
                     "fields": {"progress": random.randrange(101)}}]})),
    ]
    print("| Сценарий | Потоков | Запросов/с | p50, мс | p95, мс | p99, мс | Ошибок |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    try:
        for name, factory in scenarios:
            for threads in [int(x) for x in args.threads.split(",")]:
                r = run(session, threads, args.seconds, factory)
                print(f"| {name} | {threads} | {r['rps']:.0f} | {r['p50']:.1f} | {r['p95']:.1f} | {r['p99']:.1f} "
                      f"| {r['errors']}{' ' + str(r['codes']) if r['codes'] else ''} |", flush=True)
    finally:
        session.request("DELETE", f"/api/projects/{test_project}")


if __name__ == "__main__":
    main()
