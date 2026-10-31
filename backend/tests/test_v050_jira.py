"""0.5.0: Jira — поиск без JQL, проекты и их релизы, спринты, эпики, связи «блокирует», подсказки, источник задач плана,
другие адреса этой Jira, запасной GET-поиск и пошаговая диагностика (ADR-0007)."""

import pytest
import requests

from tests.conftest import new_client
from tests.helpers import create_project, ok_ops, task
from app.jira_client import JiraClient, JiraError, build_jql, issue_links, jql_string, parse_sprints
from app.services import jira as jira_service

BASE = "https://jira.example.ru"
OLD = "https://jira.old.example.ru"


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.is_redirect = status in (301, 302, 303, 307, 308)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _status(name, key):
    return {"name": name, "statusCategory": {"key": key}}


SPRINT_5 = ("com.atlassian.greenhopper.service.sprint.Sprint@6f[id=5,rapidViewId=1,state=CLOSED,name=Спринт 5, итоги,"
            "startDate=2026-10-05T09:00:00.000+03:00,endDate=2026-10-19T18:00:00.000+03:00,completeDate=<null>,sequence=5]")
SPRINT_6 = ("com.atlassian.greenhopper.service.sprint.Sprint@70[id=6,rapidViewId=1,state=ACTIVE,name=Спринт 6,"
            "startDate=2026-10-19T09:00:00.000+03:00,endDate=2026-11-02T18:00:00.000+03:00,completeDate=<null>,sequence=6]")


class FakeJira:
    """Jira Data Center: проекты, версии, статусы, доски и спринты, поиск по JQL (как строит «Пульс»), связи."""

    def __init__(self):
        self.calls = []
        self.block_post_search = False
        self.no_browse = False
        self.issues = {
            "ABC-1": {"summary": "Согласование", "status": _status("В работе", "indeterminate"),
                      "issuetype": {"name": "Задача"}, "duedate": "2026-06-12", "customfield_10010": [SPRINT_5]},
            "ABC-2": {"summary": "Прототип", "status": _status("Открыта", "new"), "issuetype": {"name": "История"},
                      "duedate": "2026-06-20", "customfield_10010": [SPRINT_5, SPRINT_6],
                      "fixVersions": [{"id": "100", "name": "Релиз 2", "releaseDate": "2026-12-15"}],
                      "issuelinks": [{"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                                      "inwardIssue": {"key": "ABC-1"}},
                                     {"type": {"name": "Relates", "inward": "relates to", "outward": "relates to"},
                                      "outwardIssue": {"key": "ABC-9"}}],
                      "customfield_10008": "ABC-10"},
            "ABC-3": {"summary": "Готово давно", "status": _status("Готово", "done"), "issuetype": {"name": "Задача"}},
            "ABC-10": {"summary": "Личный кабинет", "status": _status("В работе", "indeterminate"),
                       "issuetype": {"name": "Эпик"}},
        }

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json"), kwargs.get("params")))
        if url.startswith(BASE + "/rest/agile/1.0/"):
            path = url[len(BASE + "/rest/agile/1.0/"):]
            if path == "board":
                return Resp(200, {"values": [{"id": 1, "name": "Доска ABC", "type": "scrum"}]})
            if path == "board/1/sprint":
                return Resp(200, {"values": [
                    {"id": 5, "name": "Спринт 5", "state": "closed", "startDate": "2026-10-05T09:00:00.000Z",
                     "endDate": "2026-10-19T18:00:00.000Z"},
                    {"id": 6, "name": "Спринт 6", "state": "active", "startDate": "2026-10-19T09:00:00.000Z",
                     "endDate": "2026-11-02T18:00:00.000Z"}]})
            return Resp(404, {})
        if not url.startswith(BASE + "/rest/api/2/"):
            return Resp(404, {})
        path = url[len(BASE + "/rest/api/2/"):]
        if path == "myself":
            return Resp(200, {"displayName": "Сервис"})
        if path == "serverInfo":
            return Resp(200, {"version": "9.12.2", "deploymentType": "DataCenter", "baseUrl": OLD})
        if path == "mypermissions":
            return Resp(200, {"permissions": {"BROWSE_PROJECTS": {"havePermission": not self.no_browse}}})
        if path == "project":
            return Resp(200, [] if self.no_browse else [{"key": "ABC", "name": "Портал"}, {"key": "OPS", "name": "Эксплуатация"}])
        if path == "project/ABC/versions":
            return Resp(200, [{"id": "100", "name": "Релиз 2", "released": False, "releaseDate": "2026-12-15"},
                              {"id": "101", "name": "Старый", "released": True, "archived": True}])
        if path == "project/ABC/statuses":
            return Resp(200, [{"name": "Задача", "statuses": [_status("Открыта", "new"), _status("Готово", "done")]},
                              {"name": "Эпик", "statuses": [_status("Открыта", "new")]},
                              {"name": "Подзадача", "subtask": True, "statuses": []}])
        if path == "field":
            return Resp(200, [
                {"id": "duedate", "name": "Срок", "schema": {"type": "date"}},
                {"id": "customfield_10008", "name": "Epic Link", "custom": True,
                 "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"}},
                {"id": "customfield_10010", "name": "Sprint", "custom": True,
                 "schema": {"custom": "com.pyxis.greenhopper.jira:gh-sprint"}}])
        if path == "search":
            if method == "POST" and self.block_post_search:
                return Resp(405, None)  # прокси перед Jira запрещает POST
            body = kwargs.get("json") if method == "POST" else {
                "jql": kwargs["params"]["jql"], "startAt": int(kwargs["params"].get("startAt", 0)),
                "maxResults": int(kwargs["params"]["maxResults"])}
            jql = body["jql"]
            if "НЕВЕРНО" in jql:
                return Resp(400, {"errorMessages": ["Error in the JQL Query"]})
            keys = sorted(self.issues)
            if jql.startswith("key in"):
                wanted = [k.strip() for k in jql[jql.index("(") + 1:jql.index(")")].split(",")]
                keys = [k for k in keys if k in wanted]
            if "issuetype = \"Эпик\"" in jql:
                keys = [k for k in keys if self.issues[k]["issuetype"]["name"] == "Эпик"]
            if "statusCategory != 3" in jql:
                keys = [k for k in keys if self.issues[k]["status"]["statusCategory"]["key"] != "done"]
            if "text ~" in jql:
                word = jql.split('text ~ "')[1].split('"')[0].lower()
                keys = [k for k in keys if word in self.issues[k]["summary"].lower()]
            found = [{"key": k, "fields": self.issues[k]} for k in keys]
            page = found[body.get("startAt", 0):body.get("startAt", 0) + body["maxResults"]]
            return Resp(200, {"issues": page, "total": len(found)})
        if path.startswith("issue/"):
            key = path.split("/")[1]
            return Resp(200, {"key": key, "fields": self.issues[key]}) if key in self.issues else Resp(404, {})
        return Resp(404, {})


@pytest.fixture
def jira(monkeypatch):
    fake = FakeJira()
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: fake(self, method, url, **kw))
    monkeypatch.setattr(jira_service, "_down_until", 0.0)
    monkeypatch.setattr(jira_service, "_epic_fields", {})
    monkeypatch.setattr(jira_service, "_projects", {})
    monkeypatch.setattr(JiraClient, "_search_get", {})
    return fake


def _connect(admin, **extra):
    res = admin.put("/api/settings/jira", json={"baseUrl": BASE, "instanceType": "server", "token": "pat", **extra})
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _plan(admin, title="Проект Jira"):
    return create_project(admin, title)[1]


# ---------- чистые функции ----------

def test_build_jql_quotes_values_and_rejects_injection():
    jql = build_jql({"project": "abc", "text": 'кабинет" OR project = SEC', "types": ["История", 'X") OR (1=1'],
                     "categories": ["new", "indeterminate"], "version": "100", "sprint": "open", "unassigned": True,
                     "order": "due"})
    assert jql == ('project = ABC AND text ~ "кабинет\\" OR project = SEC" AND issuetype in ("История", "X\\") OR (1=1")'
                   ' AND statusCategory in (2, 4) AND fixVersion = 100 AND sprint in openSprints() AND assignee is EMPTY'
                   ' ORDER BY duedate ASC, key ASC')
    assert build_jql({"text": "abc-7"}) == "key in (ABC-7) ORDER BY Rank ASC"
    assert build_jql({"project": ["ABC", "OPS"], "onlyOpen": True}).startswith("project in (ABC, OPS) AND statusCategory != 3")
    # Все три категории — без условия; эпик — по полю Server или parent в Cloud
    assert "statusCategory" not in build_jql({"project": "ABC", "categories": ["new", "indeterminate", "done"]})
    assert "cf[10008] = ABC-10" in build_jql({"project": "ABC", "epic": "abc-10"}, epic_field="customfield_10008")
    assert "parent = ABC-10" in build_jql({"project": "ABC", "epic": "ABC-10"}, cloud=True)
    for bad in ({"project": "ABC; DROP"}, {"project": "ABC", "version": "1 OR 1=1"}, {"project": "ABC", "sprint": "5)"},
                {"project": "ABC", "epic": "ABC-1 OR x"}, {}):
        with pytest.raises(JiraError):
            build_jql(bad)
    assert jql_string('a\\b"c\n') == '"a\\\\b\\"c "'


def test_sprints_from_server_strings_and_cloud_objects():
    assert parse_sprints([SPRINT_5, SPRINT_6]) == [
        {"id": 5, "name": "Спринт 5, итоги", "state": "closed", "start": "2026-10-05", "end": "2026-10-19"},
        {"id": 6, "name": "Спринт 6", "state": "active", "start": "2026-10-19", "end": "2026-11-02"}]
    assert parse_sprints([{"id": 7, "name": "S7", "state": "future", "startDate": None, "endDate": None}]) == [
        {"id": 7, "name": "S7", "state": "future", "start": None, "end": None}]
    assert parse_sprints("мусор") == [] and parse_sprints([None, "без скобок", {"id": "x"}]) == []


def test_blocks_links_only():
    links = issue_links([
        {"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}, "inwardIssue": {"key": "abc-1"}},
        {"type": {"name": "Блокировка", "inward": "блокируется", "outward": "блокирует"}, "outwardIssue": {"key": "ABC-5"}},
        {"type": {"name": "Relates"}, "outwardIssue": {"key": "ABC-9"}},
        {"type": {"name": "Blocks"}, "inwardIssue": {"key": "not a key"}},
    ])
    assert links == {"blockedBy": ["ABC-1"], "blocks": ["ABC-5"]}


# ---------- поиск для «Задач из Jira» ----------

def test_search_by_filters_returns_sprints_versions_links_and_epics(app, admin, jira):
    _connect(admin)
    plan_id = _plan(admin)
    res = admin.post("/api/jira/search", json={"planId": plan_id, "extended": True, "epics": True,
                                               "filter": {"project": "ABC", "categories": ["new", "indeterminate"]}})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["jql"] == "project = ABC AND statusCategory in (2, 4) ORDER BY Rank ASC"
    abc2 = next(i for i in data["issues"] if i["key"] == "ABC-2")
    assert [s["name"] for s in abc2["sprints"]] == ["Спринт 5, итоги", "Спринт 6"]
    assert abc2["fixVersions"] == [{"id": "100", "name": "Релиз 2", "releaseDate": "2026-12-15"}]
    assert abc2["links"] == {"blockedBy": ["ABC-1"], "blocks": []}
    assert abc2["epic"] == "ABC-10" and data["epics"]["ABC-10"]["summary"] == "Личный кабинет"
    assert abc2["project"] == "ABC"
    # Запрос сотрудника — с полями спринта, версий и связей
    body = next(c[2] for c in jira.calls if c[0] == "POST" and c[2] and c[2]["jql"].startswith("project = ABC"))
    assert {"customfield_10010", "fixVersions", "issuelinks", "customfield_10008", "project"} <= set(body["fields"])
    # Фильтр с ошибкой — 400 с текстом, а не 502
    bad = admin.post("/api/jira/search", json={"planId": plan_id, "filter": {"project": "ABC", "version": "1;"}})
    assert bad.status_code == 400 and "версия" in bad.get_json()["message"]


def test_projects_and_meta_for_editors_only(app, admin, jira):
    _connect(admin)
    plan_id = _plan(admin)
    projects = admin.get(f"/api/jira/projects?planId={plan_id}").get_json()["projects"]
    assert projects == [{"key": "ABC", "name": "Портал"}, {"key": "OPS", "name": "Эксплуатация"}]  # по названию
    meta = admin.get(f"/api/jira/projects/ABC/meta?planId={plan_id}").get_json()
    assert meta["types"] == ["Задача", "Эпик"]
    assert {"name": "Готово", "category": "done"} in meta["statuses"]
    assert [v["name"] for v in meta["versions"]] == ["Релиз 2"]  # архивные версии не предлагаются
    assert meta["versions"][0]["url"] == f"{BASE}/projects/ABC/versions/100"
    assert [s["name"] for s in meta["sprints"]] == ["Спринт 6", "Спринт 5"]  # идущий — первым
    assert meta["epics"] == [{"key": "ABC-10", "summary": "Личный кабинет"}]  # тип «Эпик» по-русски
    assert admin.get(f"/api/jira/projects/bad%20key/meta?planId={plan_id}").status_code == 400
    # Наблюдатель плана Jira вне плана не читает
    viewer = new_client(app, "watcher", "viewer")
    assert viewer.get(f"/api/jira/projects?planId={plan_id}").status_code in (403, 404)


def test_suggest_by_words_and_key(app, admin, jira):
    _connect(admin)
    plan_id = _plan(admin)
    found = admin.post("/api/jira/suggest", json={"planId": plan_id, "text": "прото", "project": "ABC"}).get_json()
    assert [i["key"] for i in found["issues"]] == ["ABC-2"]
    found = admin.post("/api/jira/suggest", json={"planId": plan_id, "text": "abc-3"}).get_json()
    assert [i["key"] for i in found["issues"]] == ["ABC-3"]
    assert admin.post("/api/jira/suggest", json={"planId": plan_id, "text": "НЕВЕРНО"}).get_json() == {"issues": []}


def test_plan_source_counts_new_open_non_epic_issues(app, admin, jira):
    _connect(admin)
    plan_id = _plan(admin)
    assert admin.get(f"/api/plans/{plan_id}/jira/source").get_json() == {"configured": False, "newCount": 0, "new": []}
    res = admin.patch(f"/api/plans/{plan_id}", json={"settings": {"jiraSource": {"project": "abc", "jql": "x" * 2000}}})
    assert res.status_code == 200
    assert res.get_json()["settings"]["jiraSource"] == {"project": "ABC", "jql": "x" * 1000}
    admin.patch(f"/api/plans/{plan_id}", json={"settings": {"jiraSource": {"project": "ABC", "jql": ""}}})
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-1")])
    data = admin.get(f"/api/plans/{plan_id}/jira/source?refresh=1").get_json()
    # ABC-1 уже в плане, ABC-3 завершена, ABC-10 — эпик: новая только ABC-2
    assert data["newCount"] == 1 and [i["key"] for i in data["new"]] == ["ABC-2"]
    search = [c[2]["jql"] for c in jira.calls if c[0] == "POST" and c[2] and c[2]["jql"].startswith("project = ABC AND")]
    assert search[-1] == "project = ABC AND statusCategory != 3 ORDER BY Rank ASC"
    # Неверный ключ проекта в настройке не сохраняется
    res = admin.patch(f"/api/plans/{plan_id}", json={"settings": {"jiraSource": {"project": "ABC) OR (1=1", "jql": ""}}})
    assert res.get_json()["settings"]["jiraSource"]["project"] == ""


# ---------- другие адреса этой Jira ----------

def test_link_urls_recognize_rows_and_report_foreign_hosts(app, admin, jira):
    conn = _connect(admin, linkUrls=[OLD + "/", "", BASE])
    assert conn["linkUrls"] == [OLD]  # основной адрес и пустые строки не повторяются
    plan_id = _plan(admin)
    ok_ops(admin, plan_id, [
        task("t1", jiraUrl=f"{OLD}/browse/ABC-1"),
        task("t2", jiraUrl="https://jira.other.ru/browse/XYZ-5"),
        task("t3", jiraUrl="https://wiki.example.ru/page"),
    ])
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["rows"] == {"ABC-1": ["t1"]}
    assert data["issues"]["ABC-1"]["summary"] == "Согласование"
    assert data["foreign"] == [{"host": "jira.other.ru", "rows": 1}]
    assert admin.get("/api/jira").get_json()["linkUrls"] == [OLD]
    bad = admin.put("/api/settings/jira", json={"baseUrl": BASE, "instanceType": "server", "linkUrls": ["ftp://x"]})
    assert bad.status_code == 400


# ---------- устойчивость поиска ----------

def test_search_falls_back_to_get_when_post_is_blocked(app, admin, jira):
    jira.block_post_search = True
    _connect(admin)
    plan_id = _plan(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{BASE}/browse/ABC-2")])
    data = admin.get(f"/api/plans/{plan_id}/jira").get_json()
    assert data["error"] is None and data["issues"]["ABC-2"]["summary"] == "Прототип"
    get = [c for c in jira.calls if c[0] == "GET" and c[1].endswith("/search")]
    assert get and get[0][3]["validateQuery"] == "false" and "summary" in get[0][3]["fields"]
    # Дальше этот процесс сразу идёт GET-ом
    before = len([c for c in jira.calls if c[0] == "POST"])
    admin.get(f"/api/plans/{plan_id}/jira?refresh=1")
    assert len([c for c in jira.calls if c[0] == "POST"]) == before


# ---------- проверка и диагностика ----------

def test_connection_check_warns_about_projects_and_base_url(app, admin, jira):
    jira.no_browse = True
    _connect(admin)
    res = admin.post("/api/settings/jira/test", json={}).get_json()
    assert res["ok"] and res["projects"] == 0 and res["baseUrl"] == OLD
    assert any("не видит ни одного проекта" in w for w in res["warnings"])
    assert any(OLD in w and "Другие адреса" in w for w in res["warnings"])


def test_diagnose_explains_why_nothing_loads(app, admin, jira):
    jira.no_browse = True
    _connect(admin)
    plan_id = _plan(admin)
    ok_ops(admin, plan_id, [task("t1", jiraUrl=f"{OLD}/browse/ABC-1")])
    steps = {s["id"]: s for s in admin.post("/api/settings/jira/diagnose", json={"issueKey": "ABC-2"}).get_json()["steps"]}
    assert steps["network"]["status"] == "ok" and "Data Center 9.12.2" in steps["network"]["detail"]
    assert steps["baseurl"]["status"] == "warn"
    assert steps["login"]["status"] == "ok"
    assert steps["permission"]["status"] == "error"
    assert steps["projects"]["status"] == "error"
    assert steps["search"]["status"] == "ok"
    assert steps["fields"]["status"] == "ok" and "customfield_10010" in steps["fields"]["detail"]
    assert steps["issue"]["status"] == "ok" and "20.06.2026" in steps["issue"]["detail"]
    assert steps["links"]["status"] == "warn" and "jira.old.example.ru (1)" in steps["links"]["detail"]
    assert "1 строка ссылается" in steps["links"]["detail"]
    # После добавления старого адреса — строки распознаются
    _connect(admin, linkUrls=[OLD])
    steps = {s["id"]: s for s in admin.post("/api/settings/jira/diagnose", json={}).get_json()["steps"]}
    assert steps["links"]["status"] == "ok" and "1 (1 задача)" in steps["links"]["detail"]
    assert steps["baseurl"]["status"] == "ok"
    # Только администратору
    editor = new_client(app, "ed", "editor")
    assert editor.post("/api/settings/jira/diagnose", json={}).status_code == 403


def test_diagnose_without_connection_and_wrong_password(app, admin, jira, monkeypatch):
    steps = admin.post("/api/settings/jira/diagnose", json={}).get_json()["steps"]
    assert steps[0]["status"] == "error" and "не настроено" in steps[0]["detail"]
    _connect(admin)
    original = jira.__call__

    def deny(session, method, url, **kwargs):
        if url.endswith("/myself"):
            return Resp(401, {"errorMessages": ["no"]})
        return original(session, method, url, **kwargs)
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: deny(self, method, url, **kw))
    steps = {s["id"]: s for s in admin.post("/api/settings/jira/diagnose", json={}).get_json()["steps"]}
    assert steps["login"]["status"] == "error" and "401" in steps["login"]["detail"]
    assert "projects" not in steps  # дальше без входа не проверяется
