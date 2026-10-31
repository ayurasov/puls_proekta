"""0.5.0: ответная интеграция с «По Делу» (ADR-0010) — ключ «Пульса», API для «По Делу» с правами сотрудника,
ключ «По Делу», проверка связи и задачи «По Делу», связанные с проектами и строками планов."""

import json

import pytest
import requests

from tests.conftest import make_user, new_client
from tests.helpers import create_project, ok_ops, task
from app.extensions import db
from app.models import AccessRule, AppSetting, User
from app.services import podelu_links

PODELU = "https://podelu.example.ru"
PODELU_KEY = "podelu_" + "k" * 43


def _issue(admin):
    res = admin.post("/api/settings/podelu/links/key")
    assert res.status_code == 200, res.get_json()
    return res.get_json()["key"]


def _get(client, path, token, **params):
    return client.get(f"/api/integration/v1{path}", headers={"Authorization": f"Bearer {token}"}, query_string=params)


# ---------- ключ «Пульса» и API для «По Делу» ----------

def test_inbound_key_is_shown_once_stored_as_hash_and_revocable(app, admin, client):
    assert _get(client, "/ping", "puls_nothing_issued_yet_123456").status_code == 401
    key = _issue(admin)
    assert key.startswith("puls_") and len(key) > 40
    with app.app_context():
        stored = json.dumps(db.session.get(AppSetting, "podelu_links").value)
    assert key not in stored and key[:12] in stored
    info = admin.get("/api/settings/podelu/links").get_json()["inbound"]
    assert info["prefix"] == key[:12] and info["lastUsedAt"] is None and "hash" not in info
    anon = app.test_client()
    ping = _get(anon, "/ping", key)
    assert ping.status_code == 200 and ping.get_json()["app"] == "puls"
    assert admin.get("/api/settings/podelu/links").get_json()["inbound"]["lastUsedAt"]
    # Без ключа, с чужим, с префиксом без хвоста, по cookie сессии — 401
    assert anon.get("/api/integration/v1/ping").status_code == 401
    assert _get(anon, "/ping", key + "x").status_code == 401
    assert _get(anon, "/ping", "puls_").status_code == 401
    assert admin.get("/api/integration/v1/projects").status_code == 401
    # Новый ключ заменяет прежний; отзыв — и новый перестаёт действовать
    second = _issue(admin)
    assert _get(anon, "/ping", key).status_code == 401 and _get(anon, "/ping", second).status_code == 200
    assert admin.delete("/api/settings/podelu/links/key").status_code == 200
    assert _get(anon, "/ping", second).status_code == 401
    # Только администратору
    editor = new_client(app, "ed", "editor")
    assert editor.post("/api/settings/podelu/links/key").status_code == 403
    assert editor.get("/api/settings/podelu/links").status_code == 403


def test_projects_follow_employee_access(app, admin):
    key = _issue(admin)
    portal_id, portal_plan = create_project(admin, "Портал поставщиков")
    create_project(admin, "Секретный проект")
    ok_ops(admin, portal_plan, [task("g1", type="group", name="Этап 1"), task("t1", name="Монтаж", parentKey="g1"),
                                {"op": "item.create", "item": {"key": "m1", "type": "milestone", "name": "Сдача",
                                                               "date": "2026-06-01"}}])
    uid = make_user(app, "ivanov", "editor", email="Ivanov@Company.ru", podelu_id="0f8fad5b-d9cb-469f-a165-70867728950e")
    make_user(app, "gone", "editor", email="gone@company.ru", active=False)
    with app.app_context():
        db.session.add(AccessRule(user_id=uid, project_id=portal_id, level="view"))
        db.session.commit()
    anon = app.test_client()
    # По id «По Делу», логину без учёта регистра и email — только доступный проект
    for params in ({"userId": "0F8FAD5B-D9CB-469F-A165-70867728950E"}, {"login": "IVANOV"}, {"email": "ivanov@company.ru"}):
        data = _get(anon, "/projects", key, **params).get_json()
        assert data["userFound"] is True and [p["title"] for p in data["projects"]] == ["Портал поставщиков"], params
    project = _get(anon, "/projects", key, login="ivanov").get_json()["projects"][0]
    assert project["path"] == f"/projects/{portal_id}" and project["planId"] == portal_plan
    assert project["total"] == 1 and project["nextMilestone"] is None  # веха в прошлом
    # Неизвестный и отключённый сотрудник — ничего; без контекста — все проекты
    assert _get(anon, "/projects", key, login="nobody").get_json() == {"projects": [], "userFound": False}
    assert _get(anon, "/projects", key, login="gone").get_json()["userFound"] is False
    everything = _get(anon, "/projects", key).get_json()
    assert everything["userFound"] is None and len(everything["projects"]) == 2
    # Поиск по названию и ids
    assert [p["title"] for p in _get(anon, "/projects", key, q="секрет").get_json()["projects"]] == ["Секретный проект"]
    assert [p["id"] for p in _get(anon, "/projects", key, ids=f"{portal_id},999,x").get_json()["projects"]] == [portal_id]
    # Подробности и строки плана — с проверкой доступа
    detail = _get(anon, f"/projects/{portal_id}", key, login="ivanov").get_json()["project"]
    assert detail["plans"] == [{"id": portal_plan, "title": detail["plans"][0]["title"], "main": True}]
    secret_id = everything["projects"][1]["id"] if everything["projects"][0]["id"] == portal_id else everything["projects"][0]["id"]
    assert _get(anon, f"/projects/{secret_id}", key, login="ivanov").status_code == 404
    assert _get(anon, f"/projects/{secret_id}/items", key, login="ivanov").status_code == 404
    items = _get(anon, f"/projects/{portal_id}/items", key, login="ivanov").get_json()
    assert items["plan"]["id"] == portal_plan
    assert [(i["key"], i["type"], i["group"]) for i in items["items"]] == [("g1", "group", ""), ("t1", "task", "Этап 1"),
                                                                          ("m1", "milestone", "")]
    assert items["items"][1]["path"] == f"/projects/{portal_id}/{portal_plan}?item=t1"
    assert [i["key"] for i in _get(anon, f"/projects/{portal_id}/items", key, q="монт").get_json()["items"]] == ["t1"]
    assert [i["key"] for i in _get(anon, f"/projects/{portal_id}/items", key, key="m1").get_json()["items"]] == ["m1"]
    assert _get(anon, f"/projects/{portal_id}/items", key, plan="x").status_code == 400
    assert _get(anon, f"/projects/{portal_id}/items", key, plan="99999").status_code == 404


# ---------- ключ «По Делу», проверка связи, показ задач ----------

class FakePodelu:
    def __init__(self):
        self.calls = []
        self.links_enabled = True
        self.status = 200
        self.tasks = [
            {"id": "t-1", "title": "Подготовить смету", "status": "in_progress", "statusLabel": "В работе", "done": False,
             "assignee": {"id": "u1", "name": "Иванов И. И."}, "due": "2026-11-05", "path": "/my-tasks?task=t-1",
             "planId": None, "itemKey": None, "linkedBy": "Петров П.", "linkedAt": "2026-10-31T10:00:00Z"},
            {"id": "t-2", "title": "Согласовать монтаж", "status": "done", "statusLabel": "Выполнена", "done": True,
             "due": None, "path": "//evil.example/x", "planId": 1, "itemKey": "t1", "itemName": "Монтаж"},
        ]

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("params"), session.headers.get("Authorization")))
        assert not kwargs.get("allow_redirects")

        class R:
            pass
        r = R()
        r.is_redirect = False
        r.close = lambda: None
        auth_ok = session.headers.get("Authorization") == f"Bearer {PODELU_KEY}"
        if not auth_ok:
            body, r.status_code = {"error": "unauthorized", "message": "Неверный ключ"}, 401
        elif self.status != 200:
            body, r.status_code = {"error": "integration_disabled", "message": "Ссылки выключены"}, self.status
        elif url.endswith("/api/integrations/puls/ping"):
            body, r.status_code = {"ok": True, "app": "podelu", "name": "По Делу", "version": "1.5.0",
                                   "linksEnabled": self.links_enabled}, 200
        elif "/api/integrations/puls/projects/" in url:
            item = (kwargs.get("params") or {}).get("item")
            body = {"tasks": [t for t in self.tasks if not item or t["itemKey"] == item], "truncated": False}
            r.status_code = 200
        elif url.endswith("/api/integrations/puls/summary"):
            body, r.status_code = {"counts": {"1": {"total": 2, "open": 1}, "x": {}}}, 200
        else:
            body, r.status_code = {"error": "not_found"}, 404
        raw = json.dumps(body).encode()

        class Raw:
            def read(self, n, decode_content=True):
                return raw[:n]
        r.raw = Raw()
        return r


@pytest.fixture
def podelu(monkeypatch):
    fake = FakePodelu()
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: fake(self, "GET", url, **kw))
    return fake


def test_outbound_key_check_then_enable(app, admin, podelu):
    # Ключ — только «podelu_…»; без проверки включить нельзя
    bad = admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU, "key": "puls_wrongprefix_123456789"})
    assert bad.status_code == 400 and "podelu_" in bad.get_json()["message"]
    res = admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU, "key": PODELU_KEY, "enabled": True})
    assert res.status_code == 409 and res.get_json()["error"] == "check_required"
    saved = admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU + "/", "key": PODELU_KEY}).get_json()
    assert saved["hasKey"] and saved["baseUrl"] == PODELU and not saved["enabled"] and saved["checkedAt"] is None
    with app.app_context():
        assert PODELU_KEY not in json.dumps(db.session.get(AppSetting, "podelu_links").value)
    assert PODELU_KEY not in admin.get("/api/settings/podelu/links").get_data(as_text=True)
    # Проверка: «По Делу» отвечает, ссылки у неё выключены — предупреждение
    podelu.links_enabled = False
    check = admin.post("/api/settings/podelu/links/test", json={}).get_json()
    assert check["ok"] and check["version"] == "1.5.0" and check["remembered"]
    assert any("выключены" in w for w in check["warnings"]) and any("Ключ «Пульса»" in w for w in check["warnings"])
    assert podelu.calls[-1][3] == f"Bearer {PODELU_KEY}"
    res = admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU, "enabled": True})
    assert res.status_code == 200 and res.get_json()["enabled"]
    # Смена адреса без ключа — отказ; смена ключа сбрасывает проверку и выключает показ до новой проверки
    assert admin.put("/api/settings/podelu/links", json={"baseUrl": "https://other.example.ru", "enabled": True}).status_code == 400
    other_key = "podelu_" + "z" * 43
    res = admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU, "key": other_key, "enabled": True})
    assert res.status_code == 409
    # Неверный ключ — понятная ошибка
    admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU, "key": other_key})
    fail = admin.post("/api/settings/podelu/links/test", json={})
    assert fail.status_code == 502 and "отклонила ключ" in fail.get_json()["message"]


def _enable(admin):
    admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU, "key": PODELU_KEY})
    assert admin.post("/api/settings/podelu/links/test", json={}).status_code == 200
    assert admin.put("/api/settings/podelu/links", json={"baseUrl": PODELU, "enabled": True}).get_json()["enabled"]


def test_project_tasks_for_project_members_only(app, admin, podelu):
    project_id, plan_id = create_project(admin, "Портал")
    assert admin.get(f"/api/projects/{project_id}/podelu-tasks").get_json() == {"enabled": False, "tasks": [], "error": None}
    _enable(admin)
    assert admin.get("/api/podelu/links").get_json() == {"enabled": True, "baseUrl": PODELU}
    data = admin.get(f"/api/projects/{project_id}/podelu-tasks").get_json()
    assert data["enabled"] and [t["title"] for t in data["tasks"]] == ["Подготовить смету", "Согласовать монтаж"]
    first, second = data["tasks"]
    assert first["url"] == f"{PODELU}/my-tasks?task=t-1" and first["assignee"] == {"name": "Иванов И. И."}
    assert second["url"] is None  # ссылка не на адрес «По Делу» не показывается
    assert second["itemKey"] == "t1" and second["planId"] == 1
    only_row = admin.get(f"/api/projects/{project_id}/podelu-tasks?item=t1").get_json()["tasks"]
    assert [t["id"] for t in only_row] == ["t-2"] and podelu.calls[-1][2]["item"] == "t1"
    # Сотрудник без доступа к проекту — 404; с доступом — видит
    stranger = new_client(app, "stranger", "editor")
    assert stranger.get(f"/api/projects/{project_id}/podelu-tasks").status_code == 404
    uid = make_user(app, "member", "viewer")
    with app.app_context():
        db.session.add(AccessRule(user_id=uid, project_id=project_id, level="view"))
        db.session.commit()
    member = app.test_client()
    member.post("/api/auth/login", json={"login": "member", "password": "password-123"})
    assert len(member.get(f"/api/projects/{project_id}/podelu-tasks").get_json()["tasks"]) == 2
    # «По Делу» выключила ссылки: сотруднику — общий текст, администратору — подробный
    podelu.status = 403
    assert "не отвечает" in member.get(f"/api/projects/{project_id}/podelu-tasks").get_json()["error"]
    assert "403" in admin.get(f"/api/projects/{project_id}/podelu-tasks").get_json()["error"]
    # Сводка — только по доступным проектам
    podelu.status = 200
    summary = member.get(f"/api/podelu/links/summary?projects={project_id},999").get_json()
    assert summary["enabled"] and summary["counts"] == ({"1": {"total": 2, "open": 1}} if project_id == 1 else {})
    with app.app_context():
        assert db.session.get(User, uid).login == "member"


def test_delete_links_removes_keys(app, admin, podelu):
    key = _issue(admin)
    _enable(admin)
    assert admin.delete("/api/settings/podelu/links").status_code == 200
    assert _get(app.test_client(), "/ping", key).status_code == 401
    data = admin.get("/api/settings/podelu/links").get_json()
    assert not data["hasKey"] and not data["enabled"] and data["inbound"] is None
    with app.app_context():
        assert podelu_links.ready() is False
