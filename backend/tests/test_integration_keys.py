"""0.6.0: ключи интеграций (ADR-0011) — несколько систем со своими ключами и правами, перенос ключа «По Делу»,
показатели проектов для «Дашбордов компании», направления, ограничение частоты по ключу."""

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from tests.conftest import login, make_user, new_client
from tests.helpers import create_project, ok_ops, task
from app.extensions import db
from app.models import AccessRule, AuditLog, Direction, IntegrationKey, utcnow
from app.services import integration_keys

BACKEND = Path(__file__).resolve().parents[1]
READ_FIELDS = {"id", "title", "subtitle", "direction", "path", "progress", "start", "end", "overdue", "total", "done",
               "planId", "planTitle", "nextMilestone"}
STATS_FIELDS = {"inwork", "blocked", "directionInfo", "updatedAt", "planWindow"}


def _get(client, path, token, **params):
    return client.get(f"/api/integration/v1{path}", headers={"Authorization": f"Bearer {token}"}, query_string=params)


def _issue(admin, name="Дашборды компании", app="dashboards", scopes=("projects.read", "projects.stats")):
    res = admin.post("/api/settings/integration-keys", json={"name": name, "app": app, "scopes": list(scopes)})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["key"], res.get_json()["item"]


def _portfolio(app, admin):
    """Направление «Внедрение» и проект с задачами в работе, заблокированной, просроченной и вехой впереди."""
    res = admin.post("/api/directions", json={"name": "Внедрение"})
    assert res.status_code == 201, res.get_json()
    direction_id = res.get_json()["id"]
    project_id, plan_id = create_project(admin, "Портал поставщиков", direction_id=direction_id)
    admin.patch(f"/api/plans/{plan_id}", json={"settings": {"range": {"start": "2026-01-01", "end": "2026-12-31"}}})
    today = utcnow().date()
    ok_ops(admin, plan_id, [
        task("t1", status="inwork", progress=40, start=str(today - timedelta(days=5)), end=str(today + timedelta(days=5))),
        task("t2", status="blocked", start=str(today - timedelta(days=9)), end=str(today - timedelta(days=2))),
        task("t3", status="done", progress=100, start="2026-01-10", end="2026-01-20"),
        {"op": "item.create", "item": {"key": "m1", "type": "milestone", "name": "Сдача",
                                       "date": str(today + timedelta(days=30))}},
    ])
    create_project(admin, "Без направления")
    return direction_id, project_id, plan_id


# ---------- выпуск, перевыпуск, права, отзыв ----------

def test_admin_issues_reissues_updates_and_revokes_keys_with_audit(app, admin, client):
    key, item = _issue(admin, scopes=["projects.stats"])
    assert key.startswith("puls_") and item["prefix"] == key[:12]
    assert item["scopes"] == ["projects.read", "projects.stats"]  # stats без read не бывает
    assert item["app"] == "dashboards" and item["active"] and item["issuedBy"] == "Администратор"
    listing = admin.get("/api/settings/integration-keys").get_json()
    assert [k["name"] for k in listing["keys"]] == ["Дашборды компании"]
    assert {s["value"] for s in listing["scopes"]} == {"projects.read", "projects.stats"}
    assert key not in json.dumps(listing)
    with app.app_context():
        row = db.session.get(IntegrationKey, item["id"])
        assert row.key_hash != key and len(row.key_hash) == 64 and key not in json.dumps(row.scopes)

    assert _get(client, "/ping", key).get_json()["key"] == {"name": "Дашборды компании",
                                                            "scopes": ["projects.read", "projects.stats"]}
    # Перевыпуск: прежний ключ сразу перестаёт действовать, запись та же
    res = admin.post(f"/api/settings/integration-keys/{item['id']}/reissue")
    new_key = res.get_json()["key"]
    assert res.get_json()["item"]["id"] == item["id"] and new_key != key
    assert _get(client, "/ping", key).status_code == 401 and _get(client, "/ping", new_key).status_code == 200
    # Права и название меняются без нового ключа
    res = admin.patch(f"/api/settings/integration-keys/{item['id']}", json={"name": "Дашборды", "scopes": ["projects.read"]})
    assert res.get_json()["item"]["scopes"] == ["projects.read"] and res.get_json()["item"]["name"] == "Дашборды"
    assert "inwork" not in json.dumps(_get(client, "/projects", new_key).get_json())
    # Отзыв: ключ не действует, запись остаётся в списке, изменить и перевыпустить нельзя
    res = admin.delete(f"/api/settings/integration-keys/{item['id']}")
    assert res.get_json()["item"]["active"] is False and res.get_json()["item"]["revokedBy"] == "Администратор"
    assert _get(client, "/ping", new_key).status_code == 401
    assert admin.post(f"/api/settings/integration-keys/{item['id']}/reissue").status_code == 400
    assert admin.patch(f"/api/settings/integration-keys/{item['id']}", json={"scopes": ["projects.read"]}).status_code == 400
    assert admin.get("/api/settings/integration-keys").get_json()["keys"][0]["revokedAt"]
    # Реестр изменений: раздел «Интеграции», без самого ключа
    with app.app_context():
        entries = db.session.execute(select(AuditLog).where(AuditLog.section == "Интеграции")
                                     .order_by(AuditLog.id)).scalars().all()
        assert [(e.action, e.field) for e in entries] == [("create", "key"), ("update", "key"), ("update", "name"),
                                                          ("update", "scopes"), ("delete", "key")]
        assert not any(k in json.dumps([e.old_value + e.new_value for e in entries]) for k in (key, new_key))
    assert admin.get("/api/audit?section=Интеграции").status_code == 200


def test_key_validation_and_admin_only(app, admin):
    for body in ({"name": "", "app": "other", "scopes": ["projects.read"]},
                 {"name": "X", "app": "other", "scopes": []},
                 {"name": "X", "app": "other", "scopes": ["projects.write"]}):
        assert admin.post("/api/settings/integration-keys", json=body).status_code == 400, body
    assert admin.post("/api/settings/integration-keys", json={"name": "X", "app": "evil", "scopes": ["projects.read"]}) \
        .status_code in (400, 422)
    assert admin.post("/api/settings/integration-keys/999/reissue").status_code == 404
    editor = new_client(app, "ed", "editor")
    assert editor.get("/api/settings/integration-keys").status_code == 403
    assert editor.post("/api/settings/integration-keys", json={"name": "X", "scopes": ["projects.read"]}).status_code == 403


def test_scope_is_enforced_per_endpoint(app, admin, client):
    _portfolio(app, admin)
    with app.app_context():
        key = "puls_" + "s" * 43
        db.session.add(IntegrationKey(name="Будущая система", app="other", scopes=["future.scope"],
                                      key_hash=integration_keys._hash(key), prefix=key[:12]))
        db.session.commit()
    assert _get(client, "/ping", key).status_code == 200  # ping — любому действующему ключу
    for path in ("/projects", "/projects/1", "/projects/1/items", "/directions"):
        res = _get(client, path, key)
        assert res.status_code == 403 and res.get_json()["error"] == "insufficient_scope", path
    assert client.get("/api/integration/v1/projects").status_code == 401


# ---------- показатели для «Дашбордов» и прежние поля для «По Делу» ----------

def test_stats_scope_adds_fields_and_podelu_key_keeps_its_contract(app, admin, client):
    direction_id, project_id, plan_id = _portfolio(app, admin)
    podelu_key = admin.post("/api/settings/podelu/links/key").get_json()["key"]
    dash_key, _item = _issue(admin)

    podelu = _get(client, "/projects", podelu_key).get_json()["projects"]
    assert all(set(p) == READ_FIELDS for p in podelu)  # «По Делу» получает ровно прежние поля

    dashboards = {p["title"]: p for p in _get(client, "/projects", dash_key).get_json()["projects"]}
    portal = dashboards["Портал поставщиков"]
    assert set(portal) == READ_FIELDS | STATS_FIELDS
    assert (portal["total"], portal["inwork"], portal["blocked"], portal["done"], portal["overdue"]) == (3, 1, 1, 1, 1)
    assert portal["progress"] == round((40 + 0 + 100) / 3)
    assert portal["direction"] == "Внедрение" and portal["directionInfo"] == {"id": direction_id, "name": "Внедрение"}
    assert portal["nextMilestone"]["name"] == "Сдача"
    assert portal["planWindow"] == {"start": "2026-01-01", "end": "2026-12-31"}
    assert portal["updatedAt"].endswith("Z")
    assert dashboards["Без направления"]["directionInfo"] is None
    detail = _get(client, f"/projects/{project_id}", dash_key).get_json()["project"]
    assert detail["inwork"] == 1 and detail["plans"][0]["id"] == plan_id
    assert "inwork" not in _get(client, f"/projects/{project_id}", podelu_key).get_json()["project"]


def test_directions_follow_employee_access(app, admin, client):
    direction_id, project_id, _plan_id = _portfolio(app, admin)
    hidden = admin.post("/api/directions", json={"name": "Закрытое"}).get_json()["id"]
    create_project(admin, "Секрет", direction_id=hidden)
    key, _item = _issue(admin)
    everything = _get(client, "/directions", key).get_json()
    assert {d["name"]: d["projects"] for d in everything["directions"]} == {"Внедрение": 1, "Закрытое": 1}
    assert everything["withoutDirection"] == 1 and everything["userFound"] is None
    uid = make_user(app, "ivanov", "viewer", email="ivanov@company.ru")
    with app.app_context():
        db.session.add(AccessRule(user_id=uid, project_id=project_id, level="view"))
        db.session.commit()
        assert db.session.get(Direction, direction_id).name == "Внедрение"
    scoped = _get(client, "/directions", key, email="IVANOV@company.ru").get_json()
    assert [d["name"] for d in scoped["directions"]] == ["Внедрение"] and scoped["withoutDirection"] == 0
    assert _get(client, "/directions", key, login="nobody").get_json() == {"directions": [], "withoutDirection": 0,
                                                                          "userFound": False}


def test_last_used_is_written_at_most_once_a_minute(app, admin, client):
    key, item = _issue(admin)
    assert _get(client, "/ping", key).status_code == 200
    with app.app_context():
        first = db.session.get(IntegrationKey, item["id"]).last_used_at
        assert first is not None
    _get(client, "/ping", key)
    with app.app_context():
        assert db.session.get(IntegrationKey, item["id"]).last_used_at == first
        row = db.session.get(IntegrationKey, item["id"])
        row.last_used_at = first - timedelta(minutes=2)
        db.session.commit()
    _get(client, "/ping", key)
    with app.app_context():
        assert db.session.get(IntegrationKey, item["id"]).last_used_at > first - timedelta(minutes=2)


# ---------- ограничение частоты по ключу ----------

@pytest.fixture
def limited_app(tmp_path):
    from app import create_app
    from app.cli import seed_calendar

    app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'limited.db'}", "RATELIMIT_ENABLED": True})
    with app.app_context():
        db.create_all()
        seed_calendar()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_rate_limit_is_per_key_and_bad_keys_share_the_address_limit(limited_app):
    make_user(limited_app, "admin", "admin")
    admin = limited_app.test_client()
    login(admin, "admin")
    first, _ = _issue(admin, name="Первая", app="other", scopes=["projects.read"])
    second, _ = _issue(admin, name="Вторая", app="other", scopes=["projects.read"])
    client = limited_app.test_client()
    statuses = [_get(client, "/ping", first).status_code for _ in range(121)]
    assert statuses[:120] == [200] * 120 and statuses[120] == 429
    # Другой ключ с того же адреса — свой счётчик
    assert _get(client, "/ping", second).status_code == 200
    # Неверные ключи — общий счётчик адреса, а не новый на каждую попытку
    bad = [_get(client, "/ping", f"puls_{i:040d}").status_code for i in range(121)]
    assert bad[:120] == [401] * 120 and bad[120] == 429
    assert _get(client, "/ping", second).status_code == 200


# ---------- миграция 0006: ключ «По Делу» переходит в таблицу ----------

def test_migration_moves_podelu_key_and_rolls_back(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", url)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = url
    cwd = os.getcwd()
    os.chdir(BACKEND)
    try:
        command.upgrade(cfg, "0005_podelu_links")
        engine = create_engine(url)
        inbound = {"hash": "a" * 64, "prefix": "puls_AbCdEfG", "createdAt": "2026-10-31T10:00:00Z",
                   "lastUsedAt": "2026-10-31T11:00:00Z"}
        value = {"baseUrl": "https://podelu.example.ru", "enabled": True, "inbound": inbound}
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO app_settings (key, value, updated_at) VALUES ('podelu_links', :v, "
                              "CURRENT_TIMESTAMP)"), {"v": json.dumps(value)})
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT name, app, scopes, key_hash, prefix, issued_at, last_used_at, revoked_at "
                                     "FROM integration_keys")).all()
            setting = json.loads(conn.execute(text("SELECT value FROM app_settings WHERE key = 'podelu_links'"))
                                 .scalar_one())
        assert len(rows) == 1
        name, app_name, scopes, key_hash, prefix, issued_at, last_used_at, revoked_at = rows[0]
        assert (name, app_name, json.loads(scopes), key_hash, prefix) == ("По Делу", "podelu", ["projects.read"],
                                                                          "a" * 64, "puls_AbCdEfG")
        assert str(issued_at).startswith("2026-10-31 10:00") and str(last_used_at).startswith("2026-10-31 11:00")
        assert revoked_at is None
        assert "inbound" not in setting and setting["baseUrl"] == "https://podelu.example.ru"
        # Откат — ключ возвращается в настройку
        command.downgrade(cfg, "0005_podelu_links")
        with engine.begin() as conn:
            setting = json.loads(conn.execute(text("SELECT value FROM app_settings WHERE key = 'podelu_links'"))
                                 .scalar_one())
        assert setting["inbound"]["hash"] == "a" * 64 and setting["inbound"]["prefix"] == "puls_AbCdEfG"
        assert setting["inbound"]["createdAt"] == "2026-10-31T10:00:00Z"
        engine.dispose()
    finally:
        os.chdir(cwd)
