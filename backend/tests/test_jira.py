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
                {"id": "customfield_10008", "name": "Epic Link", "custom": True,
                 "schema": {"type": "any", "custom": "com.pyxis.greenhopper.jira:gh-epic-link"}},
            ])
        if path == "serverInfo":
            return FakeResponse(200, {"version": "8.20.10", "deploymentType": "Server", "serverTitle": "Jira"})
        if path == "search":
            body = kwargs["json"]
            jql = body["jql"]
            # Как Jira Server/DC: validateQuery — только логическое значение (строку «warn» понимает лишь Cloud)
            validate = body.get("validateQuery", True)
            if not isinstance(validate, bool):
                return FakeResponse(400, {"errorMessages": [
                    f"Can not construct instance of java.lang.Boolean from String value '{validate}': only \"true\" "
                    "or \"false\" recognized (through reference chain: SearchRequestBean[\"validateQuery\"])"]})
            if jql.startswith("key in"):
                keys = [k.strip() for k in jql[jql.index("(") + 1:jql.rindex(")")].split(",")]
                unknown = [k for k in keys if k not in self.issues]
                if validate and unknown:
                    return FakeResponse(400, {"errorMessages": [
                        f"An issue with key '{unknown[0]}' does not exist for field 'key'."]})
            elif "BADFIELD" in jql and validate:
                return FakeResponse(400, {"errorMessages": [
                    "Field 'BADFIELD' does not exist or you do not have permission to view it."]})
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
    monkeypatch.setattr(jira_service, "_down_since", 0.0)
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
    assert res.get_json() == {"ok": True, "user": "Сервисная учётка", "version": "8.20.10", "deploymentType": "Server"}
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
    jira_service.reset_backoff()
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
    project_id, plan_id = create_project(admin)
    res = admin.post("/api/jira/search", json={"planId": plan_id, "keys": "abc-2 ABC-1, ABC-404 мусор"}).get_json()
    assert [i["key"] for i in res["issues"]] == ["ABC-2", "ABC-1"]
    assert res["missing"] == ["ABC-404"] and res["invalid"] == ["мусор"]
    res = admin.post("/api/jira/search", json={"planId": plan_id, "jql": "project = ABC"}).get_json()
    assert [i["key"] for i in res["issues"]] == ["ABC-1", "ABC-2"]
    assert admin.post("/api/jira/search", json={"planId": plan_id}).status_code == 400
    # Задачи Jira видны только тому, кто может править план (ADR-0007)
    outsider = new_client(app, "outsider", "editor")
    assert outsider.post("/api/jira/search", json={"planId": plan_id, "keys": "ABC-1"}).status_code == 404
    viewer_id = make_user(app, "nablyudatel", "viewer")
    admin.post("/api/access", json={"userIds": [viewer_id], "projectIds": [project_id], "level": "edit"})
    from tests.conftest import login
    viewer = app.test_client()
    login(viewer, "nablyudatel")
    assert viewer.post("/api/jira/search", json={"planId": plan_id, "keys": "ABC-1"}).status_code == 403
    assert admin.post("/api/jira/search", json={"keys": "ABC-1"}).status_code == 400


def test_search_without_connection(app, admin):
    _, plan_id = create_project(admin)
    assert admin.post("/api/jira/search", json={"planId": plan_id, "keys": "ABC-1"}).status_code == 409
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


def test_review_pasted_token_with_newline_is_trimmed_and_never_logged(app, admin, jira, caplog):
    """Токен из буфера с переводом строки: сохраняется без него; заголовок с управляющими символами не уходит."""
    res = admin.put("/api/settings/jira", json={"baseUrl": BASE, "instanceType": "server", "token": "SECRETPAT\n"})
    assert res.status_code == 200
    jira.add("ABC-1")
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1")])
    assert admin.get(f"/api/plans/{plan_id}/jira").status_code == 200
    assert jira.auth == "Bearer SECRETPAT"
    res = admin.put("/api/settings/jira", json={"baseUrl": BASE, "instanceType": "server", "token": "A\x00B"})
    assert res.status_code == 400
    assert "SECRETPAT" not in caplog.text


def test_review_allowed_hosts_cannot_be_bypassed(app, admin, jira, monkeypatch):
    monkeypatch.setenv("JIRA_ALLOWED_HOSTS", ".company.ru")
    for url in ("https://evil.example\\.company.ru", "https://evil.example%2f.company.ru", "https://evil.example"):
        res = admin.put("/api/settings/jira", json={"baseUrl": url, "login": "svc", "token": "pw"})
        assert res.status_code == 400, url
    assert not jira.calls


def test_review_bad_row_links_do_not_break_the_plan(app, admin, jira):
    _connect(admin)
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}:99999/browse/A-1"),
                            task("t2", jiraUrl=f"{BASE}/browse/{'A' * 120}-1")])
    res = admin.get(f"/api/plans/{plan_id}/jira")
    assert res.status_code == 200 and res.get_json()["rows"] == {}


def test_review_cloud_rejected_batch_is_split_not_read_one_by_one(app, admin, jira):
    """Jira Cloud отклоняет весь пакет из-за одного ошибочного ключа: пакет делится пополам, а не читается по одной."""
    _connect(admin)
    for i in range(100):
        jira.add(f"ABC-{i}")
    original = jira.__call__

    def strict(session, method, url, **kwargs):
        body = kwargs.get("json") or {}
        if url.endswith("/search") and "NOPE-1" in body.get("jql", ""):
            jira.calls.append((method, url, body))
            return FakeResponse(400, {"errorMessages": ["An issue with key 'NOPE-1' does not exist"]})
        return original(session, method, url, **kwargs)
    jira.__call__ = strict
    import requests as req
    req.Session.request = lambda self, method, url, **kw: strict(self, method, url, **kw)
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task(f"t{i}", jiraUrl=f"{BASE}/browse/ABC-{i}") for i in range(99)]
           + [task("bad", jiraUrl=f"{BASE}/browse/NOPE-1")])
    jira.calls.clear()
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["issues"]["NOPE-1"] is None and data["issues"]["ABC-5"]["key"] == "ABC-5"
    assert len(jira.calls) < 25, len(jira.calls)


def test_review_wrong_password_backs_off(app, admin, jira):
    _connect(admin)
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1")])
    app.config["JIRA_CACHE_SECONDS"] = 0
    original = jira.__call__
    import requests as req
    req.Session.request = lambda self, method, url, **kw: (jira.calls.append((method, url, None)), FakeResponse(401, {}))[1]
    for _ in range(5):
        admin.get(f"/api/plans/{plan_id}/jira")
    assert len(jira.calls) == 1
    req.Session.request = lambda self, method, url, **kw: original(self, method, url, **kw)


# ---------- 0.3.1: Jira Server ----------

def _searches(jira):
    return [c for c in jira.calls if c[1].endswith("/search")]


def test_v031_server_reads_keys_in_one_request_without_string_validate_query(app, admin, jira):
    """Jira Server отвечала 400 на validateQuery: "warn" — задачи плана не загружались, поиск по ключу писал
    «не найдены». Ключи читаются одним запросом с validateQuery: false, несуществующий ключ — не ошибка."""
    _connect(admin)
    jira.add("SRM-650")
    jira.add("SRM-651")
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/SRM-650"), task("t2", jiraUrl=f"{BASE}/browse/SRM-651"),
                            task("t3", jiraUrl=f"{BASE}/browse/NOPE-9")])
    jira.calls.clear()
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["error"] is None
    assert data["issues"]["SRM-650"]["summary"] == "Задача SRM-650" and data["issues"]["NOPE-9"] is None
    searches = _searches(jira)
    assert len(searches) == 1 and searches[0][2]["validateQuery"] is False
    res = admin.post("/api/jira/search", json={"planId": plan_id, "keys": "SRM-650", "refresh": True}).get_json()
    assert [i["key"] for i in res["issues"]] == ["SRM-650"] and res["missing"] == []


def test_v031_jql_is_validated_by_jira_and_error_is_readable(app, admin, jira):
    _connect(admin)
    jira.add("ABC-1")
    _, plan_id = create_project(admin)
    res = admin.post("/api/jira/search", json={"planId": plan_id, "jql": "component = ЕИО AND assignee = arkhipov.ao"})
    assert res.status_code == 200 and "validateQuery" not in _searches(jira)[-1][2]
    res = admin.post("/api/jira/search", json={"planId": plan_id, "jql": "BADFIELD = 1"})
    assert res.status_code == 400
    assert res.get_json()["message"] == (
        "Jira не приняла запрос: Field 'BADFIELD' does not exist or you do not have permission to view it.")


def test_v031_connection_check_runs_a_real_search(app, admin, jira):
    """«Соединение установлено» — только если поиск задач работает тем же запросом, что и для планов."""
    _connect(admin)
    res = admin.post("/api/settings/jira/test").get_json()
    assert res["ok"] and res["version"] == "8.20.10"
    probe = _searches(jira)[-1][2]
    assert probe["validateQuery"] is False and probe["maxResults"] == 1
    original = jira.__call__
    import requests as req

    def broken_search(session, method, url, **kwargs):
        if url.endswith("/search"):
            return FakeResponse(400, {"errorMessages": ["Search is broken"]})
        return original(session, method, url, **kwargs)
    req.Session.request = lambda self, method, url, **kw: broken_search(self, method, url, **kw)
    res = admin.post("/api/settings/jira/test")
    assert res.status_code == 502
    assert "Вход выполнен (Сервисная учётка), но поиск задач в Jira не работает" in res.get_json()["message"]


def test_v031_single_rejected_key_is_read_directly_not_hidden(app, admin, jira):
    """Jira отклоняет запрос даже с одним ключом: задача читается напрямую, а не помечается «нет в Jira»."""
    _connect(admin)
    jira.add("ABC-1")
    original = jira.__call__
    import requests as req

    def rejecting_search(session, method, url, **kwargs):
        if url.endswith("/search"):
            jira.calls.append((method, url, kwargs.get("json")))
            return FakeResponse(400, {"errorMessages": ["Unsupported search"]})
        return original(session, method, url, **kwargs)
    req.Session.request = lambda self, method, url, **kw: rejecting_search(self, method, url, **kw)
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1"), task("t2", jiraUrl=f"{BASE}/browse/NOPE-2")])
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["issues"]["ABC-1"]["summary"] == "Задача ABC-1" and data["issues"]["NOPE-2"] is None


def test_v031_missing_issue_is_rechecked_sooner_than_found(app, admin, jira, monkeypatch):
    _connect(admin)
    jira.add("ABC-1")
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1"), task("t2", jiraUrl=f"{BASE}/browse/ABC-2")])
    assert admin.get(f"/api/plans/{plan_id}/jira").get_json()["issues"]["ABC-2"] is None
    monkeypatch.setattr(jira_service, "MISSING_CACHE_SECONDS", 0)
    jira.add("ABC-2")
    jira.calls.clear()
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["issues"]["ABC-2"]["key"] == "ABC-2"
    assert [c[2]["jql"] for c in _searches(jira)] == ["key in (ABC-2)"]


def test_v040_plan_jira_auto_fields_are_validated(app, admin):
    """Автоперенос из Jira — настройка плана: только известные поля, по умолчанию выключен."""
    _, plan_id = create_project(admin)
    assert admin.get(f"/api/plans/{plan_id}").get_json()["plan"]["settings"]["jiraAuto"] == []
    ok_ops(admin, plan_id, [{"op": "plan.update", "fields": {"settings": {"jiraAuto": ["dates", "мусор", "status", "status"]}}}])
    assert admin.get(f"/api/plans/{plan_id}").get_json()["plan"]["settings"]["jiraAuto"] == ["status", "dates"]



def test_v040_import_groups_by_epics(app, admin, jira):
    """«Задачи из Jira» по эпикам: Epic Link (Server/DC) у задач и названия эпиков отдельным запросом."""
    _connect(admin)
    jira.add("ABC-100", summary="Эпик: Личный кабинет", issuetype={"name": "Epic"})
    jira.add("ABC-1", customfield_10008="ABC-100")
    jira.add("ABC-2", customfield_10008="ABC-100")
    jira.add("ABC-3")
    _, plan_id = create_project(admin)
    res = admin.post("/api/jira/search", json={"planId": plan_id, "keys": "ABC-1 ABC-2 ABC-3 NOPE-1", "epics": True})
    data = res.get_json()
    assert res.status_code == 200, data
    assert {i["key"]: i["epic"] for i in data["issues"]} == {"ABC-1": "ABC-100", "ABC-2": "ABC-100", "ABC-3": None}
    assert data["missing"] == ["NOPE-1"]
    assert data["epics"]["ABC-100"]["summary"] == "Эпик: Личный кабинет"
    requested = [c[2]["fields"] for c in _searches(jira) if "ABC-1" in c[2]["jql"]][0]
    assert "customfield_10008" in requested and "parent" in requested
    # Эпик в результатах запроса — сам эпик, а не задача
    res = admin.post("/api/jira/search", json={"planId": plan_id, "jql": "project = ABC", "epics": True}).get_json()
    assert [i["key"] for i in res["issues"] if i["isEpic"]] == ["ABC-100"] and "ABC-100" in res["epics"]


def test_v040_cloud_epic_is_parent():
    from app.jira_client import epic_key
    story = {"parent": {"key": "web-7", "fields": {"issuetype": {"name": "Epic", "hierarchyLevel": 1}}}}
    subtask = {"parent": {"key": "WEB-8", "fields": {"issuetype": {"name": "Story", "hierarchyLevel": 0}}}}
    assert epic_key(story, {}) == "WEB-7"
    assert epic_key(subtask, {}) is None
    assert epic_key({"customfield_1": "abc-5"}, {"epicField": "customfield_1"}) == "ABC-5"
