"""Интеграция с «По Делу» (ADR-0009): подключение, вход по CSRF и cookie, загрузка структуры и сотрудников.

Поддельная «По Делу» ведёт себя как todo-planner 1.4 в production: CSRF-токен, вход с X-CSRF-Token и Referer
(WTF_CSRF_SSL_STRICT), cookie todo_planner_session, права org.manage и users.manage, выгрузки — настоящие файлы
«По Делу» из tests/fixtures/todo_planner.
"""

import io
import json
import uuid
from pathlib import Path

import pytest
import requests
from sqlalchemy import func, select

from tests.conftest import make_user, new_client
from tests.helpers import create_project
from app import podelu_client
from app.extensions import db
from app.models import AppSetting, AuditLog, Department, DepartmentAccessRule, DepartmentHead, User, UserAvatar
from app.podelu_client import SESSION_COOKIE
from scripts.org_transfer import run

BASE = "https://podelu.example.ru"
PASSWORD = "Сервисный-пароль-42"
FIXTURES = Path(__file__).parent / "fixtures" / "todo_planner"


def person_id(email):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, email.lower()))


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status_code = status
        self.headers = dict(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.headers.setdefault("Content-Type", "application/json")
        self.body = body
        self.is_redirect = status in (301, 302, 303, 307, 308) and "Location" in self.headers

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]

    def close(self):
        pass


class FakePodelu:
    """todo-planner: /api/auth/csrf-token, /login, /me, /logout, /org/export, /users/export, /users/admin/all."""

    def __init__(self, base=BASE):
        self.base = base
        self.host = base.split("://", 1)[1]
        self.token = "csrf-1"
        self.calls = []
        self.requests = []
        self.sessions = {}
        self.logouts = 0
        self.secure_cookie = False
        self.down = False
        self.overrides = {}
        admin = {"id": person_id("svc@vector.test"), "name": "Сервис Пульса", "login": "svc-puls", "email": "svc@vector.test",
                 "globalRole": "user", "capabilities": ["org.manage", "users.manage"], "isActive": True}
        weak = {**admin, "id": person_id("weak@vector.test"), "name": "Без прав", "login": "weak", "capabilities": []}
        self.accounts = {"svc-puls": {"password": PASSWORD, "user": admin}, "weak": {"password": PASSWORD, "user": weak}}
        self.structure = json.loads((FIXTURES / "todo_structure.json").read_text(encoding="utf-8"))
        self.zip = (FIXTURES / "todo_employees.zip").read_bytes()
        self.document = json.loads((FIXTURES / "todo_employees.json").read_text(encoding="utf-8"))
        self.people = [{"id": person_id(e["email"]), "email": e["email"], "login": e["login"], "isSystem": False}
                       for e in self.document["employees"]]
        self.people.append({"id": person_id("system@podelu"), "email": "system@podelu", "login": "system", "isSystem": True})

    def __call__(self, session, method, url, **kwargs):
        headers = {**session.headers, **(kwargs.get("headers") or {})}
        assert kwargs.get("allow_redirects") is False and kwargs.get("timeout")
        self.requests.append({"method": method, "url": url, "headers": headers, "json": kwargs.get("json")})
        if self.down:
            raise requests.exceptions.ConnectionError("refused")
        if not url.startswith(self.base + "/api/"):
            return FakeResponse(404, {"error": "not_found"})
        path = url[len(self.base + "/api/"):]
        self.calls.append((method, path))
        if path in self.overrides:
            return self.overrides[path]
        if path == "auth/csrf-token":
            return FakeResponse(200, {"csrfToken": self.token})
        if method == "POST":
            if headers.get("X-CSRF-Token") != self.token:
                return FakeResponse(400, {"error": "csrf_error", "message": "The CSRF token is missing."})
            # production «По Делу»: HTTPS-запрос с изменением данных — только с Referer того же сайта
            if self.base.startswith("https://") and not (headers.get("Referer") or "").startswith(self.base + "/"):
                return FakeResponse(400, {"error": "csrf_error", "message": "The referrer header is missing."})
        if path == "auth/login":
            body = kwargs.get("json") or {}
            account = self.accounts.get(body.get("login"))
            if account is None or account["password"] != body.get("password"):
                return FakeResponse(401, {"error": "invalid_credentials", "message": "Неверный login или пароль"})
            sid = f"sid-{len(self.sessions) + 1}"
            self.sessions[sid] = body["login"]
            session.cookies.set(SESSION_COOKIE, sid, domain=self.host, path="/", secure=self.secure_cookie)
            return FakeResponse(200, {"user": account["user"]})
        sid = session.cookies.get(SESSION_COOKIE, domain=self.host)
        if path == "auth/logout":
            self.sessions.pop(sid, None)
            self.logouts += 1
            return FakeResponse(200, {"message": "logged_out"})
        login = self.sessions.get(sid)
        if login is None:
            return FakeResponse(401, {"error": "auth_required", "message": "Требуется авторизация"})
        user = self.accounts[login]["user"]
        caps = user["capabilities"] if user["globalRole"] != "admin" else ["org.manage", "users.manage"]
        if path == "auth/me":
            return FakeResponse(200, user)
        if path == "org/export":
            if "org.manage" not in caps:
                return FakeResponse(403, {"error": "permission_denied", "message": "Изменять структуру может администратор"})
            return FakeResponse(200, self.structure)
        if path.startswith("users/export"):
            if "users.manage" not in caps:
                return FakeResponse(403, {"error": "permission_denied", "message": "Выгружать сотрудников может администратор"})
            if (kwargs.get("params") or {}).get("format") == "zip":
                return FakeResponse(200, self.zip, {"Content-Type": "application/zip"})
            return FakeResponse(200, self.document)
        if path == "users/admin/all":
            return FakeResponse(200, self.people)
        return FakeResponse(404, {"error": "not_found"})


@pytest.fixture
def podelu(monkeypatch):
    fake = FakePodelu()
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: fake(self, method, url, **kw))
    monkeypatch.setattr(podelu_client.time, "sleep", lambda _seconds: None)
    return fake


def _connect(admin, **extra):
    body = {"baseUrl": BASE, "login": "svc-puls", "password": PASSWORD, **extra}
    res = admin.put("/api/settings/podelu", json=body)
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _sync(admin, **body):
    return admin.post("/api/org/sync/podelu", json=body)


def _counts(app):
    with app.app_context():
        return (db.session.query(Department).count(), db.session.query(User).count(), db.session.query(AuditLog).count())


# ---------------------------------------------------------------- подключение
def test_connection_check_logs_in_with_csrf_and_referer_and_logs_out(app, admin, podelu, caplog):
    saved = _connect(admin)
    assert saved == {"baseUrl": BASE, "login": "svc-puls", "acceptInvalidCerts": False, "hasPassword": True}
    res = admin.post("/api/settings/podelu/test", json={})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["user"] == {"name": "Сервис Пульса", "login": "svc-puls", "role": "user"}
    assert body["rights"] == {"structure": True, "employees": True} and body["problem"] is None
    assert podelu.calls == [("GET", "auth/csrf-token"), ("POST", "auth/login"), ("GET", "auth/me"), ("POST", "auth/logout")]
    login = next(r for r in podelu.requests if r["url"].endswith("/auth/login"))
    assert login["headers"]["X-CSRF-Token"] == "csrf-1"
    assert login["headers"]["Referer"] == BASE + "/" and login["headers"]["Origin"] == BASE
    assert login["json"] == {"login": "svc-puls", "password": PASSWORD}
    assert podelu.logouts == 1 and podelu.sessions == {}
    # Пароль не возвращается, не пишется в реестр и в журнал, в базе — только зашифрованным
    assert PASSWORD not in json.dumps(admin.get("/api/settings/podelu").get_json(), ensure_ascii=False)
    with app.app_context():
        stored = db.session.get(AppSetting, "podelu").value
        assert PASSWORD not in json.dumps(stored, ensure_ascii=False) and stored["passwordEncrypted"]
        entries = db.session.execute(select(AuditLog).where(AuditLog.entity == "По Делу")).scalars().all()
        assert [(e.action, e.field) for e in entries] == [("create", "baseUrl, login, password")]
        assert all(PASSWORD not in f"{e.old_value}{e.new_value}" for e in entries)
    assert PASSWORD not in caplog.text


def test_form_is_checked_before_saving_and_with_saved_password(app, admin, podelu):
    res = admin.post("/api/settings/podelu/test", json={"baseUrl": BASE, "login": "svc-puls", "password": PASSWORD})
    assert res.status_code == 200 and res.get_json()["user"]["name"] == "Сервис Пульса"
    assert admin.get("/api/settings/podelu").get_json() is None   # проверка ничего не сохраняет
    _connect(admin)
    # Без пароля в форме — сохранённый, но только для того же адреса и логина
    assert admin.post("/api/settings/podelu/test", json={"baseUrl": BASE, "login": "svc-puls"}).status_code == 200
    other = admin.post("/api/settings/podelu/test", json={"baseUrl": "https://evil.example.ru", "login": "svc-puls"})
    assert other.status_code == 502 and "Укажите пароль" in other.get_json()["message"]
    assert not any("evil" in r["url"] for r in podelu.requests)


def test_wrong_password_and_disabled_account(app, admin, podelu):
    _connect(admin, password="не тот пароль")
    res = admin.post("/api/settings/podelu/test", json={})
    assert res.status_code == 502
    assert res.get_json()["message"] == "«По Делу» отклонила вход: неверный логин или пароль учётной записи подключения"
    assert "не тот пароль" not in json.dumps(res.get_json(), ensure_ascii=False)
    podelu.overrides["auth/login"] = FakeResponse(403, {"error": "account_disabled", "message": "Аккаунт деактивирован"})
    assert admin.post("/api/settings/podelu/test", json={}).get_json()["message"] == \
        "Учётная запись подключения отключена в «По Делу»"
    podelu.overrides["auth/login"] = FakeResponse(429, {"error": "rate_limited"})
    assert "(429)" in admin.post("/api/settings/podelu/test", json={}).get_json()["message"]


def test_account_without_rights_is_reported(app, admin, podelu):
    _connect(admin, login="weak")
    body = admin.post("/api/settings/podelu/test", json={}).get_json()
    assert body["rights"] == {"structure": False, "employees": False}
    assert body["problem"].startswith("Вход выполнен, но у учётной записи нет прав на выгрузку структуры и сотрудников")
    res = _sync(admin)
    assert res.status_code == 502
    assert res.get_json()["message"].startswith("У учётной записи нет прав на выгрузку структуры в «По Делу»")
    assert podelu.calls[-1] == ("POST", "auth/logout")   # выход и при ошибке
    # Только права на структуру — сотрудники не выгружаются
    podelu.accounts["weak"]["user"]["capabilities"] = ["org.manage"]
    assert "выгрузку сотрудников" in _sync(admin).get_json()["message"]
    # Ответ 403 самой «По Делу» объясняется так же
    podelu.accounts["weak"]["user"]["capabilities"] = ["org.manage", "users.manage"]
    podelu.overrides["org/export"] = FakeResponse(403, {"error": "permission_denied", "message": "нет"})
    assert "нет прав на выгрузку структуры" in _sync(admin).get_json()["message"]


def test_changing_address_login_or_tls_requires_password_again(app, admin, podelu):
    _connect(admin)
    for change, message in (({"baseUrl": "https://other.example.ru"}, "Адрес «По Делу» изменён — введите пароль заново"),
                            ({"login": "other"}, "Логин изменён — введите пароль заново"),
                            ({"acceptInvalidCerts": True}, "Без проверки сертификата введите пароль заново")):
        res = admin.put("/api/settings/podelu", json={"baseUrl": BASE, "login": "svc-puls", **change})
        assert res.status_code == 400 and res.get_json()["message"] == message
    # Тот же адрес и логин — пароль можно не вводить
    assert admin.put("/api/settings/podelu", json={"baseUrl": BASE + "/", "login": "svc-puls"}).status_code == 200
    for bad in ("ftp://podelu", "https://user:pw@podelu.example.ru", "https://podelu.example.ru/?a=1", "https://p\\x"):
        res = admin.put("/api/settings/podelu", json={"baseUrl": bad, "login": "svc-puls", "password": "x"})
        assert res.status_code == 400, bad
    res = admin.put("/api/settings/podelu", json={"baseUrl": BASE, "login": "svc-puls", "password": "a\x00b"})
    assert res.status_code == 400


def test_allowed_hosts(app, admin, podelu, monkeypatch):
    monkeypatch.setenv("PODELU_ALLOWED_HOSTS", ".company.ru")
    res = admin.put("/api/settings/podelu", json={"baseUrl": BASE, "login": "svc-puls", "password": PASSWORD})
    assert res.status_code == 400 and "PODELU_ALLOWED_HOSTS" in res.get_json()["message"]
    monkeypatch.setenv("PODELU_ALLOWED_HOSTS", ".example.ru")
    assert admin.put("/api/settings/podelu", json={"baseUrl": BASE, "login": "svc-puls", "password": PASSWORD}).status_code == 200


def test_network_redirect_server_and_cookie_errors(app, admin, podelu):
    _connect(admin)
    podelu.down = True
    assert "Не удалось подключиться к https://podelu.example.ru" in admin.post("/api/settings/podelu/test", json={}).get_json()["message"]
    podelu.down = False
    # Перенаправление не выполняется: пароль не уходит на другой адрес
    podelu.overrides["auth/csrf-token"] = FakeResponse(302, headers={"Location": "https://sso.example.ru/login?next=1"})
    message = admin.post("/api/settings/podelu/test", json={}).get_json()["message"]
    assert "перенаправляет запрос (302) на https://sso.example.ru/login" in message
    assert not any("sso.example.ru" in r["url"] for r in podelu.requests)
    # 503 повторяется, затем понятная ошибка
    podelu.overrides["auth/csrf-token"] = FakeResponse(503, {"error": "unavailable"})
    before = len(podelu.calls)
    assert admin.post("/api/settings/podelu/test", json={}).get_json()["message"] == "«По Делу» вернула ошибку 503 — попробуйте позже"
    assert len(podelu.calls) - before == 3
    # Вместо «По Делу» отвечает страница входа прокси
    podelu.overrides["auth/csrf-token"] = FakeResponse(200, b"<html>login</html>", {"Content-Type": "text/html"})
    assert "неожиданный ответ (/api/auth/csrf-token)" in admin.post("/api/settings/podelu/test", json={}).get_json()["message"]
    podelu.overrides.clear()
    # CSRF: без верного токена «По Делу» отвечает 400 — сообщение объясняет, что проверить
    podelu.token = "другой"
    podelu.overrides["auth/csrf-token"] = FakeResponse(200, {"csrfToken": "старый"})
    assert "защитой от подделки (CSRF)" in admin.post("/api/settings/podelu/test", json={}).get_json()["message"]


def test_secure_cookie_over_http_is_explained(app, admin, monkeypatch):
    fake = FakePodelu("http://podelu.example.ru")
    fake.secure_cookie = True
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: fake(self, method, url, **kw))
    admin.put("/api/settings/podelu", json={"baseUrl": "http://podelu.example.ru", "login": "svc-puls", "password": PASSWORD})
    message = admin.post("/api/settings/podelu/test", json={}).get_json()["message"]
    assert message.startswith("«По Делу» выдаёт cookie сессии только для HTTPS")


def test_admin_only_and_public_status(app, admin, podelu):
    viewer = new_client(app, "pd-viewer", "viewer")
    assert viewer.get("/api/podelu").get_json() == {"connected": False, "baseUrl": None}
    _connect(admin)
    assert viewer.get("/api/podelu").get_json() == {"connected": True, "baseUrl": BASE}
    for method, path in (("get", "/api/settings/podelu"), ("put", "/api/settings/podelu"), ("delete", "/api/settings/podelu"),
                         ("post", "/api/settings/podelu/test"), ("get", "/api/org/sync/podelu"),
                         ("post", "/api/org/sync/podelu")):
        assert getattr(viewer, method)(path, json={}).status_code == 403, path
    assert app.test_client().get("/api/podelu").status_code == 401
    assert admin.delete("/api/settings/podelu").status_code == 200
    assert viewer.get("/api/podelu").get_json() == {"connected": False, "baseUrl": None}
    res = _sync(admin)
    assert res.status_code == 409 and res.get_json()["error"] == "podelu_not_configured"


# ---------------------------------------------------------------- структура и сотрудники
def test_preview_does_not_write_and_replan_does_not_call_podelu(app, admin, podelu):
    _connect(admin)
    before = _counts(app)
    res = _sync(admin)
    assert res.status_code == 200, res.get_json()
    plan = res.get_json()
    assert _counts(app) == before   # проверка ничего не записывает
    assert plan["source"]["baseUrl"] == BASE and plan["source"]["account"] == "Сервис Пульса"
    assert plan["source"]["departments"] == 5 and plan["source"]["employees"] == 8 and plan["source"]["photos"] == 2
    assert plan["structure"]["summary"]["create"] == 5 and plan["structure"]["canApply"]
    # Сотрудники планируются по структуре после загрузки: новых подразделений в их плане нет
    employees = plan["employees"]
    assert employees["summary"]["create"] == 7 and employees["summary"]["createDepartments"] == 0
    records = {r["key"]: r for r in employees["records"]}
    assert records["admin@todo-planner.local"]["userId"] is not None   # администратор «Пульса» — по логину
    assert any("Администратор" in w for w in records["ceo@vector.test"]["warnings"])
    assert plan["canApply"] and len(plan["fingerprint"]) == 64
    assert set(plan["snapshot"]) == {"structure", "employees", "ids", "avatars", "source"}
    assert plan["snapshot"]["ids"]["d.orlov@vector.test"] == person_id("d.orlov@vector.test")
    assert ("GET", "users/admin/all") in podelu.calls and podelu.calls[-1] == ("POST", "auth/logout")

    # Решения по коллизиям пересчитывают план по снимку — без входа в «По Делу»
    calls = len(podelu.calls)
    decisions = {"admin@todo-planner.local": {"fields": {"name": {"choice": "incoming"}}}}
    replanned = _sync(admin, snapshot=plan["snapshot"], decisions=decisions).get_json()
    assert len(podelu.calls) == calls and replanned["fingerprint"] == plan["fingerprint"]
    conflict = {r["key"]: r for r in replanned["employees"]["records"]}["admin@todo-planner.local"]["conflicts"][0]
    assert conflict["field"] == "name" and conflict["choice"] == "incoming"
    assert _counts(app) == before
    assert _sync(admin, snapshot={"structure": []}).status_code == 400
    assert _sync(admin, apply=True).status_code == 400   # применение — только после проверки


def test_apply_imports_structure_employees_links_and_photos(app, admin, podelu):
    _connect(admin)
    plan = _sync(admin, newUsers="file").get_json()
    res = _sync(admin, apply=True, fingerprint=plan["fingerprint"], newUsers="file")
    assert res.status_code == 200, res.get_json()
    result = res.get_json()
    assert result["applied"] and result["linkedUsers"] == 8 and result["photos"] == {"saved": 2, "errors": []}
    assert podelu.logouts == 2
    with app.app_context():
        deps = {d.name: d for d in db.session.execute(select(Department)).scalars()}
        source = {d["name"]: d["key"] for d in podelu.structure["departments"]}
        # Подразделения из «По Делу» сохраняют свои UUID: id и ссылка совпадают с «По Делу»
        assert {name: (d.id, d.podelu_id, d.org_source) for name, d in deps.items()} == \
            {name: (key, key, "podelu") for name, key in source.items()}
        users = {u.login: u for u in db.session.execute(select(User)).scalars()}
        assert users["d.orlov"].podelu_id == person_id("d.orlov@vector.test") and users["d.orlov"].org_source == "podelu"
        assert users["admin"].podelu_id == person_id("admin@todo-planner.local") and users["admin"].role == "admin"
        # Права администратора из «По Делу» не выдаются
        assert users["i.sokolova"].role == "viewer" and users["a.kovaleva"].role == "manager"
        assert users["d.orlov"].department_id == source["Группа B2B"]
        assert db.session.query(UserAvatar).count() == 2
        entry = db.session.execute(select(AuditLog).where(AuditLog.entity == "Загрузка из «По Делу»")).scalar_one()
        assert entry.user_name == "admin" and "сотрудников новых 7" in entry.new_value
    # Ссылки видны в структуре — для переходов в «По Делу»
    tree = admin.get("/api/org/tree").get_json()
    orlov = next(p for p in tree["people"] if p["login"] == "d.orlov")
    assert orlov["podeluId"] == person_id("d.orlov@vector.test")
    assert {d["podeluId"] for d in tree["departments"]} == set(source.values())

    # Повторная загрузка: ничего нового, связи на месте
    again = _sync(admin).get_json()
    assert again["structure"]["summary"]["create"] == 0
    assert again["employees"]["summary"]["create"] == 0
    assert [r["key"] for r in again["employees"]["records"] if r["action"] != "unchanged"] == []


def test_person_is_matched_by_podelu_id_after_email_change(app, admin, podelu):
    _connect(admin)
    plan = _sync(admin, newUsers="file").get_json()
    assert _sync(admin, apply=True, fingerprint=plan["fingerprint"], newUsers="file").status_code == 200
    for item in podelu.document["employees"]:
        if item["login"] == "d.orlov":
            item["email"] = "dmitry.orlov@vector.test"
    for person in podelu.people:
        if person["login"] == "d.orlov":
            person["email"] = "dmitry.orlov@vector.test"
    podelu.zip = _zip_without_photos(podelu.document)
    record = {r["login"]: r for r in _sync(admin, newUsers="file").get_json()["employees"]["records"]}["d.orlov"]
    # Тот же сотрудник (по идентификатору «По Делу»), а не новый; новый email — коллизия, решает администратор
    assert record["action"] == "unchanged" and record["userId"] is not None and record["identity"] is None
    assert [c["field"] for c in record["conflicts"]] == ["email"]


def _zip_without_photos(document):
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("employees.json", json.dumps({**document, "employees": [
            {k: v for k, v in e.items() if k != "avatar"} for e in document["employees"]]}, ensure_ascii=False))
    return buffer.getvalue()


def test_apply_refuses_when_podelu_changed_after_preview(app, admin, podelu):
    _connect(admin)
    plan = _sync(admin).get_json()
    podelu.structure["departments"].append({"key": str(uuid.uuid4()), "name": "Маркетинг",
                                            "parent": podelu.structure["departments"][0]["key"], "kind": "department",
                                            "sortOrder": 3, "heads": []})
    before = _counts(app)
    res = _sync(admin, apply=True, fingerprint=plan["fingerprint"])
    assert res.status_code == 409
    body = res.get_json()
    assert body["error"] == "podelu_changed" and body["structure"]["summary"]["create"] == 6
    assert body["fingerprint"] != plan["fingerprint"] and "snapshot" in body
    assert _counts(app) == before
    assert _sync(admin, apply=True, fingerprint=body["fingerprint"]).status_code == 200


def test_without_creating_missing_only_existing_people_are_updated(app, admin, podelu):
    make_user(app, "p.ivanov", "editor", name="Иванов П.", email="p.ivanov@vector.test")
    _connect(admin)
    plan = _sync(admin, createMissing=False).get_json()
    summary = plan["employees"]["summary"]
    assert summary["create"] == 0 and summary["skip"] == 6 and plan["canApply"]
    skipped = [r for r in plan["employees"]["records"] if r["action"] == "skip"]
    assert all("не создаётся" in r["warnings"][-1] for r in skipped)
    assert _sync(admin, apply=True, fingerprint=plan["fingerprint"], createMissing=False).status_code == 200
    with app.app_context():
        assert db.session.query(User).count() == 2
        ivanov = db.session.execute(select(User).where(User.login == "p.ivanov")).scalar_one()
        assert ivanov.podelu_id == person_id("p.ivanov@vector.test") and ivanov.department_id is not None


def test_errors_in_plan_block_apply(app, admin, podelu):
    _connect(admin)
    podelu.structure["departments"][1]["kind"] = "unknown-kind"
    plan = _sync(admin).get_json()
    assert not plan["canApply"] and plan["structure"]["errors"]
    before = _counts(app)
    res = _sync(admin, apply=True, fingerprint=plan["fingerprint"])
    assert res.status_code == 409 and res.get_json()["error"] == "import_has_errors"
    assert _counts(app) == before
    podelu.structure = {"format": "something-else"}
    res = _sync(admin)
    assert res.status_code == 502 and res.get_json()["error"] == "podelu_format"


# ---------------------------------------------------------------- командная строка
def test_cli_sync_podelu_dry_run_and_apply(app, admin, podelu):
    _connect(admin)
    out = io.StringIO()
    assert run(app, ["sync-podelu", "--actor", "admin"], out=out) == 0
    summary = json.loads(out.getvalue().splitlines()[0])
    assert summary["structure"]["create"] == 5 and summary["employees"]["skip"] == 7
    assert _counts(app)[0] == 0   # без --apply ничего не пишет
    out = io.StringIO()
    assert run(app, ["sync-podelu", "--actor", "admin", "--create-missing", "--new-users", "active", "--apply"], out=out) == 0
    summary = json.loads(out.getvalue().splitlines()[0])
    assert summary["employees"]["create"] == 7
    with app.app_context():
        assert db.session.query(Department).count() == 5
        assert db.session.query(User).filter(User.podelu_id.is_not(None)).count() == 8
        assert db.session.query(User).filter(User.org_source == "podelu", User.is_active.is_(True)).count() == 7
        assert db.session.execute(select(func.count()).select_from(AuditLog)
                                  .where(AuditLog.entity == "Командная строка: sync-podelu")).scalar() == 1

    # Изменения в «По Делу»: --prefer-podelu берёт их в коллизиях, роль не трогает
    for item in podelu.document["employees"]:
        if item["login"] == "p.ivanov":
            item["position"] = "Ведущий менеджер"
            item["role"] = "admin"
    podelu.zip = _zip_without_photos(podelu.document)
    with app.app_context():
        user = db.session.execute(select(User).where(User.login == "p.ivanov")).scalar_one()
        user.position = "Менеджер (из «Пульса»)"
        db.session.commit()
    assert run(app, ["sync-podelu", "--actor", "admin", "--apply"], out=io.StringIO()) == 0
    with app.app_context():
        assert db.session.execute(select(User.position).where(User.login == "p.ivanov")).scalar_one() == \
            "Менеджер (из «Пульса»)"   # без флага коллизия остаётся значением системы
    assert run(app, ["sync-podelu", "--actor", "admin", "--prefer-podelu", "--no-photos", "--apply"], out=io.StringIO()) == 0
    with app.app_context():
        user = db.session.execute(select(User).where(User.login == "p.ivanov")).scalar_one()
        assert user.position == "Ведущий менеджер" and user.role == "viewer"


def test_cli_sync_podelu_reports_unavailable_podelu(app, admin, podelu, capsys):
    assert run(app, ["sync-podelu", "--actor", "admin"], out=io.StringIO()) == 2
    assert "Подключение к «По Делу» не настроено" in capsys.readouterr().err
    _connect(admin)
    podelu.down = True
    assert run(app, ["sync-podelu", "--actor", "admin", "--apply"], out=io.StringIO()) == 2
    assert _counts(app)[0] == 0


# ---------------------------------------------------------------- подразделения по связи с «По Делу»
def _item(podelu, name):
    return next(d for d in podelu.structure["departments"] if d["name"] == name)


def _moved(podelu, old, new):
    """Подразделение переименовано или перенесено в «По Делу»: у сотрудников в выгрузке — новые пути."""
    def fix(path):
        return new + path[len(old):] if path[:len(old)] == old else path

    for item in podelu.document["employees"]:
        item["department"] = fix(item["department"])
        for head in item["heads"]:
            head["department"] = fix(head["department"])


def _apply_sync(admin, **options):
    plan = _sync(admin, **options).get_json()
    res = _sync(admin, apply=True, fingerprint=plan["fingerprint"], **options)
    assert res.status_code == 200, res.get_json()
    return plan


def _change(plan, path):
    entry = next(d for d in plan["structure"]["departments"] if d["path"] == path)
    return entry["action"], {c["field"]: (c["old"], c["new"]) for c in entry["changes"]}


def test_department_renamed_in_podelu_is_renamed_in_place(app, admin, podelu):
    _connect(admin)
    _apply_sync(admin, newUsers="file", photos=False)
    sales = _item(podelu, "Продажи")
    project_id, _plan_id = create_project(admin, "Воронка продаж")
    granted = admin.post("/api/access/departments", json={"departmentIds": [sales["key"]], "projectIds": [project_id],
                                                          "level": "edit"})
    assert granted.status_code == 201, granted.get_json()

    sales["name"] = "Коммерция"
    _moved(podelu, ["ООО «Вектор»", "Продажи"], ["ООО «Вектор»", "Коммерция"])
    plan = _sync(admin, photos=False).get_json()
    assert plan["canApply"], plan["structure"]["errors"]
    assert plan["structure"]["summary"]["create"] == 0
    assert _change(plan, "ООО «Вектор» / Коммерция") == ("update", {"name": ("Продажи", "Коммерция")})
    assert _change(plan, "ООО «Вектор» / Коммерция / Группа B2B") == ("unchanged", {})
    assert plan["structure"]["missingInFile"] == [] and plan["structure"]["removedInPodelu"] == []
    # Сотрудники — по структуре после переименования: новых подразделений и переводов нет
    employees = plan["employees"]
    assert employees["summary"]["createDepartments"] == 0
    assert [r["key"] for r in employees["records"] if r["action"] != "unchanged"] == []
    _apply_sync(admin, photos=False)
    with app.app_context():
        row = db.session.get(Department, sales["key"])
        assert row.name == "Коммерция" and row.podelu_id == sales["key"] and db.session.query(Department).count() == 5
        kovaleva = db.session.execute(select(User).where(User.login == "a.kovaleva")).scalar_one()
        assert kovaleva.department_id == sales["key"]
        assert db.session.get(DepartmentHead, (sales["key"], kovaleva.id)).is_primary
        assert db.session.execute(select(DepartmentAccessRule.department_id)).scalars().all() == [sales["key"]]
        entry = db.session.execute(select(AuditLog).where(AuditLog.field == "name")).scalar_one()
        assert (entry.old_value, entry.new_value) == ("Продажи", "Коммерция")
    # Загрузка обычного файла по-прежнему сопоставляет по пути: другое название — новое подразделение
    bundle = json.loads(json.dumps(podelu.structure))
    next(d for d in bundle["departments"] if d["key"] == sales["key"])["name"] = "Маркетинг"
    file_plan = admin.post("/api/org/import/structure", json={"bundle": bundle}).get_json()
    assert [d["path"] for d in file_plan["departments"] if d["action"] == "create"] == \
        ["ООО «Вектор» / Маркетинг", "ООО «Вектор» / Маркетинг / Группа B2B"]
    assert "removedInPodelu" not in file_plan


def test_department_moved_in_podelu_under_renamed_and_new_departments(app, admin, podelu):
    _connect(admin)
    _apply_sync(admin, newUsers="file", photos=False)
    lab, sales, root = _item(podelu, "Лаборатория"), _item(podelu, "Продажи"), _item(podelu, "ООО «Вектор»")
    # В одной загрузке: «Продажи» переименованы, «Лаборатория» перенесена в них (родитель в файле — раньше детей)
    sales["name"] = "Коммерция"
    lab["parent"] = sales["key"]
    podelu.structure["departments"].remove(lab)
    podelu.structure["departments"].append(lab)
    _moved(podelu, ["ООО «Вектор»", "Продажи"], ["ООО «Вектор»", "Коммерция"])
    _moved(podelu, ["ООО «Вектор»", "Разработка", "Лаборатория"], ["ООО «Вектор»", "Коммерция", "Лаборатория"])
    plan = _sync(admin, photos=False).get_json()
    assert plan["canApply"], plan["structure"]["errors"]
    assert _change(plan, "ООО «Вектор» / Коммерция") == ("update", {"name": ("Продажи", "Коммерция")})
    assert _change(plan, "ООО «Вектор» / Коммерция / Лаборатория") == \
        ("update", {"parent": ("ООО «Вектор» / Разработка", "ООО «Вектор» / Коммерция")})
    assert [r["key"] for r in plan["employees"]["records"] if r["action"] != "unchanged"] == []
    _apply_sync(admin, photos=False)
    with app.app_context():
        row = db.session.get(Department, lab["key"])
        assert row.parent_id == sales["key"] and row.name == "Лаборатория"
        smirnova = db.session.execute(select(User).where(User.login == "e.smirnova")).scalar_one()
        sokolova = db.session.execute(select(User).where(User.login == "i.sokolova")).scalar_one()
        assert smirnova.department_id == lab["key"]
        assert db.session.get(DepartmentHead, (lab["key"], sokolova.id)) is not None

    # Перенос в подразделение, которое появилось в этой же загрузке
    science = {"key": str(uuid.uuid4()), "name": "Наука", "parent": root["key"], "kind": "department", "sortOrder": 5,
               "externalId": None, "heads": []}
    podelu.structure["departments"].insert(1, science)
    lab["parent"] = science["key"]
    _moved(podelu, ["ООО «Вектор»", "Коммерция", "Лаборатория"], ["ООО «Вектор»", "Наука", "Лаборатория"])
    plan = _sync(admin, photos=False).get_json()
    assert _change(plan, "ООО «Вектор» / Наука") == ("create", {})
    assert _change(plan, "ООО «Вектор» / Наука / Лаборатория") == \
        ("update", {"parent": ("ООО «Вектор» / Коммерция", "ООО «Вектор» / Наука")})
    _apply_sync(admin, photos=False)
    with app.app_context():
        assert db.session.get(Department, lab["key"]).parent_id == science["key"]
        assert db.session.get(Department, science["key"]).podelu_id == science["key"]

    # Перенос на верхний уровень
    lab["parent"] = None
    _moved(podelu, ["ООО «Вектор»", "Наука", "Лаборатория"], ["Лаборатория"])
    plan = _sync(admin, photos=False).get_json()
    assert _change(plan, "Лаборатория") == ("update", {"parent": ("ООО «Вектор» / Наука", "верхний уровень")})
    _apply_sync(admin, photos=False)
    with app.app_context():
        assert db.session.get(Department, lab["key"]).parent_id is None


def test_department_kind_and_order_change_and_sibling_name_clash(app, admin, podelu):
    _connect(admin)
    _apply_sync(admin, newUsers="file", photos=False)
    group, sales, root = _item(podelu, "Группа B2B"), _item(podelu, "Продажи"), _item(podelu, "ООО «Вектор»")
    group["kind"], group["sortOrder"] = "sector", 7
    plan = _sync(admin, photos=False).get_json()
    assert _change(plan, "ООО «Вектор» / Продажи / Группа B2B") == \
        ("update", {"kind": ("group", "sector"), "sortOrder": (0, 7)})
    _apply_sync(admin, photos=False)
    with app.app_context():
        row = db.session.get(Department, group["key"])
        assert (row.kind, row.sort_order) == ("sector", 7)

    # Переименование в название подразделения «Пульса», которого нет в «По Делу», — ошибка плана, а не два «Маркетинга»
    assert admin.post("/api/departments", json={"name": "Маркетинг", "parentId": root["key"]}).status_code == 201
    sales["name"] = "Маркетинг"
    _moved(podelu, ["ООО «Вектор»", "Продажи"], ["ООО «Вектор»", "Маркетинг"])
    plan = _sync(admin, photos=False).get_json()
    assert not plan["canApply"]
    assert plan["structure"]["errors"] == ["ООО «Вектор» / Маркетинг: на этом уровне в «Пульсе» уже есть подразделение "
                                           "«Маркетинг», которого нет в «По Делу», — переименуйте или перенесите его"]
    before = _counts(app)
    assert _sync(admin, apply=True, fingerprint=plan["fingerprint"], photos=False).status_code == 409
    assert _counts(app) == before


def test_department_removed_in_podelu_stays_and_relinks_by_path(app, admin, podelu):
    _connect(admin)
    _apply_sync(admin, newUsers="file", photos=False)
    group = _item(podelu, "Группа B2B")
    podelu.structure["departments"].remove(group)
    plan = _sync(admin, photos=False).get_json()
    assert plan["canApply"]
    assert plan["structure"]["removedInPodelu"] == ["ООО «Вектор» / Продажи / Группа B2B"]
    assert plan["structure"]["summary"]["removedInPodelu"] == 1 and plan["structure"]["missingInFile"] == []
    _apply_sync(admin, photos=False)
    with app.app_context():
        row = db.session.get(Department, group["key"])
        assert row is not None and row.podelu_id == group["key"]   # ничего не удаляется
        orlov = db.session.execute(select(User).where(User.login == "d.orlov")).scalar_one()
        assert orlov.department_id == group["key"]

    # В «По Делу» на том же месте появилось подразделение с другим UUID: по пути, связь переходит к нему
    again = {**group, "key": str(uuid.uuid4())}
    podelu.structure["departments"].append(again)
    plan = _sync(admin, photos=False).get_json()
    assert _change(plan, "ООО «Вектор» / Продажи / Группа B2B") == ("unchanged", {})
    assert plan["structure"]["removedInPodelu"] == []
    _apply_sync(admin, photos=False)
    with app.app_context():
        assert db.session.get(Department, group["key"]).podelu_id == again["key"]
        assert db.session.query(Department).count() == 5


def test_cli_sync_podelu_renames_and_moves(app, admin, podelu):
    _connect(admin)
    assert run(app, ["sync-podelu", "--actor", "admin", "--create-missing", "--no-photos", "--apply"],
               out=io.StringIO()) == 0
    sales, lab = _item(podelu, "Продажи"), _item(podelu, "Лаборатория")
    sales["name"] = "Коммерция"
    lab["parent"] = sales["key"]
    podelu.structure["departments"].remove(lab)
    podelu.structure["departments"].append(lab)
    out = io.StringIO()
    assert run(app, ["sync-podelu", "--actor", "admin", "--no-photos", "--apply"], out=out) == 0
    assert json.loads(out.getvalue().splitlines()[0])["structure"]["update"] == 2
    with app.app_context():
        assert db.session.get(Department, sales["key"]).name == "Коммерция"
        assert db.session.get(Department, lab["key"]).parent_id == sales["key"]
        assert db.session.query(Department).count() == 5
