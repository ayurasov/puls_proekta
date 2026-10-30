"""Интеграция с Jira (ADR-0007): подключение, задачи строк плана, кэш, поиск для «Добавить из Jira»."""

import json

import pytest
import requests

from tests.conftest import make_user, new_client
from tests.helpers import create_project, ok_ops, task
from app.extensions import db
from app.jira_client import issue_key_from_url, parse_issue_keys
from app.models import AppSetting, JiraIssue
from app.services import jira as jira_service

BASE = "https://jira.example.ru"


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.is_redirect = status in (301, 302, 303, 307, 308)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeJira:
    """Jira Server: /myself, /search (key in …), /issue/KEY, /field."""

    def __init__(self):
        self.issues = {}
        self.calls = []
        self.down = False
        self.auth = None

    def add(self, key, **fields):
        base = {"summary": f"Задача {key}", "status": {"name": "В работе", "statusCategory": {"key": "indeterminate"}},
                "assignee": {"displayName": "Иванов И."}, "duedate": "2026-11-20", "timespent": 3600 * 6,
                "timeestimate": 3600 * 2, "resolutiondate": None}
        base.update(fields)
        self.issues[key] = base

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json")))
        self.auth = session.auth or session.headers.get("Authorization")
        if self.down:
            raise requests.exceptions.ConnectionError("refused")
        if not url.startswith(BASE + "/rest/api/2/"):
            return FakeResponse(404, {})
        path = url[len(BASE + "/rest/api/2/"):]
        if path == "myself":
            return FakeResponse(200, {"displayName": "Сервисная учётка"})
        if path == "field":
            return FakeResponse(200, [
                {"id": "duedate", "name": "Срок", "custom": False, "schema": {"type": "date"}},
                {"id": "customfield_10015", "name": "Start date", "custom": True, "schema": {"type": "date"}},
                {"id": "summary", "name": "Тема", "schema": {"type": "string"}},
            ])
        if path == "search":
            body = kwargs["json"]
            jql = body["jql"]
            if jql.startswith("key in"):
                keys = [k.strip() for k in jql[jql.index("(") + 1:jql.rindex(")")].split(",")]
            else:
                keys = sorted(self.issues)
            found = [{"key": k, "fields": self.issues[k]} for k in keys if k in self.issues]
            page = found[body["startAt"]:body["startAt"] + body["maxResults"]]
            return FakeResponse(200, {"issues": page, "total": len(found)})
        if path.startswith("issue/"):
            key = path.split("/")[1]
            return FakeResponse(200, {"key": key, "fields": self.issues[key]}) if key in self.issues else FakeResponse(404, {})
        return FakeResponse(404, {})


@pytest.fixture
def jira(monkeypatch):
    fake = FakeJira()
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: fake(self, method, url, **kw))
    monkeypatch.setattr(jira_service, "_down_until", 0.0)
    return fake


def _connect(admin, **extra):
    res = admin.put("/api/settings/jira", json={"baseUrl": BASE + "/", "instanceType": "server_basic",
                                                "login": "svc-puls", "token": "секрет", **extra})
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def test_issue_keys_from_links():
    assert issue_key_from_url(f"{BASE}/browse/ABC-12", BASE) == "ABC-12"
    assert issue_key_from_url(f"{BASE}/browse/abc-12?focusedCommentId=1", BASE) == "ABC-12"
    assert issue_key_from_url(f"{BASE}/secure/RapidBoard.jspa?rapidView=1&selectedIssue=XY_1-7", BASE) == "XY_1-7"
    assert issue_key_from_url(f"{BASE}/projects/ABC/issues/ABC-3", BASE) == "ABC-3"
    assert issue_key_from_url("https://other.example.ru/browse/ABC-12", BASE) is None
    assert issue_key_from_url(f"{BASE}/browse/ABC", BASE) is None
    assert issue_key_from_url("https://jira.example.ru/jira/browse/A-1", "https://jira.example.ru/jira") == "A-1"
    assert issue_key_from_url("https://jira.example.ru/browse/A-1", "https://jira.example.ru/jira") is None
    assert parse_issue_keys("abc-1, ABC-1;\nXYZ-3 bad") == (["ABC-1", "XYZ-3"], ["bad"])


def test_connection_is_admin_only_and_token_never_returned(app, admin, jira):
    editor = new_client(app, "petrov", "editor")
    assert editor.get("/api/settings/jira").status_code == 403
    assert editor.put("/api/settings/jira", json={"baseUrl": BASE}).status_code == 403
    conn = _connect(admin)
    assert conn == {"baseUrl": BASE, "instanceType": "server_basic", "login": "svc-puls", "acceptInvalidCerts": False,
                    "hasToken": True, "startField": "", "endField": "duedate"}
    with app.app_context():
        stored = db.session.get(AppSetting, "jira").value
        assert "секрет" not in json.dumps(stored, ensure_ascii=False)
    assert "секрет" not in admin.get("/api/settings/jira").get_data(as_text=True)
    assert editor.get("/api/jira").get_json() == {"connected": True, "baseUrl": BASE, "hasStartField": False}

    # Проверка без нового пароля — сохранённым; пароль в UTF-8 (кириллица)
    res = admin.post("/api/settings/jira/test", json={"baseUrl": BASE, "instanceType": "server_basic", "login": "svc-puls"})
    assert res.get_json() == {"ok": True, "user": "Сервисная учётка"}
    assert jira.auth == ("svc-puls".encode(), "секрет".encode())
    # Сохранённый пароль не уходит на другой адрес
    res = admin.put("/api/settings/jira", json={"baseUrl": "https://evil.example.ru", "login": "svc-puls"})
    assert res.status_code == 400
    res = admin.post("/api/settings/jira/test", json={"baseUrl": "https://evil.example.ru", "login": "svc-puls"})
    assert res.status_code == 502 and not any("evil" in c[1] for c in jira.calls)
    fields = admin.post("/api/settings/jira/fields").get_json()
    assert [f["id"] for f in fields] == ["duedate", "customfield_10015"]

    assert admin.delete("/api/settings/jira").status_code == 200
    assert editor.get("/api/jira").get_json()["connected"] is False


def test_bad_addresses_rejected(app, admin, jira):
    for url in ("ftp://jira", "https://user:pw@jira.example.ru", "https://jira.example.ru/?a=1", "jira.example.ru"):
        res = admin.put("/api/settings/jira", json={"baseUrl": url, "login": "x", "token": "y"})
        assert res.status_code == 400, url


def test_plan_issues_cached_and_refreshed(app, admin, jira):
    _connect(admin, startField="customfield_10015")
    jira.add("ABC-1", customfield_10015="2026-11-02")
    jira.add("ABC-2", status={"name": "Готово", "statusCategory": {"key": "done"}}, resolutiondate="2026-11-05T12:00:00.000+0300")
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [
        task("t1", jiraUrl=f"{BASE}/browse/ABC-1"), task("t2", jiraUrl=f"{BASE}/browse/ABC-2"),
        task("t3", jiraUrl=f"{BASE}/browse/ABC-1"), task("t4", jiraUrl=f"{BASE}/browse/NOPE-9"),
        task("t5", jiraUrl="https://other.example.ru/browse/ABC-1"),
    ])
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["error"] is None and data["rows"] == {"ABC-1": ["t1", "t3"], "ABC-2": ["t2"], "NOPE-9": ["t4"]}
    one, two = data["issues"]["ABC-1"], data["issues"]["ABC-2"]
    assert data["issues"]["NOPE-9"] is None
    assert one["summary"] == "Задача ABC-1" and one["statusCategory"] == "indeterminate" and one["assignee"] == "Иванов И."
    assert one["start"] == "2026-11-02" and one["due"] == "2026-11-20" and one["progress"] == 75
    assert one["url"] == f"{BASE}/browse/ABC-1" and one["spentHours"] == 6.0
    assert two["statusCategory"] == "done" and two["resolved"] == "2026-11-05"
    searches = len([c for c in jira.calls if c[1].endswith("/search")])

    # Повторное открытие — из кэша, без запроса к Jira
    jira.add("ABC-1", summary="Переименована")
    assert admin.get(f"/api/plans/{plan_id}/jira").get_json()["issues"]["ABC-1"]["summary"] == "Задача ABC-1"
    assert len([c for c in jira.calls if c[1].endswith("/search")]) == searches
    # «Обновить из Jira» — свежие данные
    assert admin.get(f"/api/plans/{plan_id}/jira?refresh=1").get_json()["issues"]["ABC-1"]["summary"] == "Переименована"


def test_jira_down_keeps_cached_data_and_does_not_hang_every_open(app, admin, jira):
    _connect(admin)
    jira.add("ABC-1")
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1"), task("t2", jiraUrl=f"{BASE}/browse/ABC-2")])
    admin.get(f"/api/plans/{plan_id}/jira")
    jira.down = True
    jira.calls.clear()
    data = admin.get(f"/api/plans/{plan_id}/jira?refresh=1").get_json()
    assert "Не удалось подключиться" in data["error"] and data["issues"]["ABC-1"]["summary"] == "Задача ABC-1"
    # Минуту после отказа устаревшие задачи не запрашиваются у Jira при каждом открытии
    app.config["JIRA_CACHE_SECONDS"] = 0
    jira.calls.clear()
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["error"] and not jira.calls


def test_errors_for_staff_hide_jira_address(app, admin, jira):
    _connect(admin)
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1")])
    jira.down = True
    data = admin.get(f"/api/plans/{plan_id}/jira?refresh=1").get_json()
    assert BASE in data["error"]
    project_id = admin.get(f"/api/plans/{plan_id}").get_json()["plan"]["projectId"]
    user_id = make_user(app, "sidorov", "editor")
    admin.post("/api/access", json={"userIds": [user_id], "projectIds": [project_id], "level": "view"})
    from tests.conftest import login
    staff = app.test_client()
    login(staff, "sidorov")
    data = staff.get(f"/api/plans/{plan_id}/jira?refresh=1").get_json()
    assert data["error"] and BASE not in data["error"] and "администратору" in data["error"]


def test_plan_issues_need_plan_access(app, admin, jira):
    _connect(admin)
    _, plan_id = create_project(admin)
    outsider = new_client(app, "chuzhoy", "editor")
    assert outsider.get(f"/api/plans/{plan_id}/jira").status_code == 404


def test_search_by_keys_and_jql(app, admin, jira):
    _connect(admin)
    jira.add("ABC-1")
    jira.add("ABC-2")
    res = admin.post("/api/jira/search", json={"keys": "abc-2 ABC-1, ABC-404 мусор"}).get_json()
    assert [i["key"] for i in res["issues"]] == ["ABC-2", "ABC-1"]
    assert res["missing"] == ["ABC-404"] and res["invalid"] == ["мусор"]
    res = admin.post("/api/jira/search", json={"jql": "project = ABC"}).get_json()
    assert [i["key"] for i in res["issues"]] == ["ABC-1", "ABC-2"]
    viewer = new_client(app, "nablyudatel", "viewer")
    assert viewer.post("/api/jira/search", json={"keys": "ABC-1"}).status_code == 403
    assert admin.post("/api/jira/search", json={}).status_code == 400


def test_search_without_connection(app, admin):
    assert admin.post("/api/jira/search", json={"keys": "ABC-1"}).status_code == 409
    _, plan_id = create_project(admin)
    assert admin.get(f"/api/plans/{plan_id}/jira").get_json()["connected"] is False


def test_changing_jira_address_clears_cache(app, admin, jira):
    _connect(admin)
    jira.add("ABC-1")
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1")])
    admin.get(f"/api/plans/{plan_id}/jira")
    with app.app_context():
        assert db.session.query(JiraIssue).count() == 1
    _connect(admin, startField="customfield_10015")
    with app.app_context():
        assert db.session.query(JiraIssue).count() == 0
