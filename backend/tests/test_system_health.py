"""0.7.2: состояние системы (ADR-0014)."""

from datetime import datetime, timedelta

from sqlalchemy import text

from app.extensions import db
from app.models import AppSetting, User
from app.services import system_health
from tests.conftest import new_client
from tests.test_jira import _connect, jira  # noqa: F401 — фикстура поддельной Jira

NOW = datetime(2026, 11, 5, 12, 0, 0)


def _items(body):
    return {item["key"]: item for item in body["items"]}


def _overview(app, now=NOW):
    with app.test_request_context():
        return system_health.overview(now)


def test_admin_always_sees_it_others_need_the_capability(app, admin):
    body = admin.get("/api/system/health").get_json()
    assert [i["key"] for i in body["items"]] == list(system_health.COMPONENTS)
    assert body["status"] in ("ok", "warn", "error") and body["checkedAt"].endswith("Z")
    editor = new_client(app, "editor", "editor")
    denied = editor.get("/api/system/health")
    assert denied.status_code == 403 and "Смотрит состояние системы" in denied.get_json()["message"]
    with app.app_context():
        user_id = db.session.query(User).filter_by(login="editor").one().id
    admin.patch(f"/api/users/{user_id}", json={"capabilities": ["system.health"]})
    assert editor.get("/api/system/health").status_code == 200
    # «Проверить подключения» без настроенных подключений — пустой список проверок и та же сводка
    checked = editor.post("/api/system/health/check").get_json()
    assert checked["checks"] == [] and len(checked["items"]) == len(system_health.COMPONENTS)


def test_cards_without_connections_are_off_and_overall_ignores_off(app, admin):
    items = _items(_overview(app))
    for key in ("backups", "jira", "podelu", "ldap", "keys"):
        assert items[key]["status"] == "off", key
    assert items["errors"]["summary"] == "За сутки ошибок нет"
    assert items["calendar"]["status"] == "ok" and "2026 и 2027" in items["calendar"]["summary"]
    assert items["users"]["summary"].startswith("Активных: 1")
    assert "назначьте второго" in items["users"]["details"][0]
    assert items["jira"]["link"] == "/admin/integrations/jira" and items["jira"]["capability"] == "modules.manage"
    assert _overview(app)["status"] == "ok"


def test_jira_failures_and_recovery_are_recorded(app, admin, jira):  # noqa: F811
    _connect(admin)
    jira.down = True
    checked = admin.post("/api/system/health/check").get_json()
    assert checked["checks"][0]["key"] == "jira" and checked["checks"][0]["ok"] is False
    card = _items(checked)["jira"]
    assert card["status"] == "error" and "ошибка" in card["summary"]
    assert checked["status"] == "error"
    jira.down = False
    card = _items(admin.post("/api/system/health/check").get_json())["jira"]
    assert card["status"] == "ok" and card["summary"].startswith("Последнее успешное обращение")


def test_successful_calls_are_written_at_most_once_a_minute(app):
    at = NOW
    with app.test_request_context():
        system_health._record([("jira", True, "", at)])
        system_health._record([("jira", True, "", at + timedelta(seconds=20))])
        assert system_health._setting("health_state", {})["jira"]["lastOkAt"] == "2026-11-05T12:00:00Z"
        system_health._record([("jira", False, "нет ответа", at + timedelta(seconds=30))])
        state = system_health._setting("health_state", {})["jira"]
        assert state["last"] == "error" and state["lastError"] == "нет ответа"


def test_server_errors_are_kept_and_counted(app):
    with app.test_request_context("/api/plans/1/ops", method="POST"):
        system_health.note_server_error(KeyError("x"))
        system_health.flush()
    items = _items(_overview(app, datetime.utcnow()))
    assert items["errors"]["status"] == "warn" and items["errors"]["summary"] == "Ошибок за сутки: 1"
    assert "POST /api/plans/1/ops · KeyError: 'x'" in items["errors"]["details"][0]


def test_backup_status_from_backup_container(app):
    def backup(**info):
        with app.app_context():
            row = db.session.get(AppSetting, "backup_status") or AppSetting(key="backup_status")
            row.value = info
            db.session.add(row)
            db.session.commit()
        return _items(_overview(app))["backups"]

    fresh = backup(ok=True, at="2026-11-05T03:00:05Z", lastOkAt="2026-11-05T03:00:05Z", size=5 * 1024 ** 2,
                   file="puls_20261105-030000_daily.dump", count=14, totalBytes=70 * 1024 ** 2)
    assert fresh["status"] == "ok" and fresh["summary"] == "Последняя копия 8 ч назад, 5,0 МБ"
    assert "Копий хранится: 14, всего 70,0 МБ" in fresh["details"]
    assert backup(ok=True, lastOkAt="2026-11-04T03:00:00Z")["status"] == "warn"
    assert backup(ok=True, lastOkAt="2026-11-02T03:00:00Z")["status"] == "error"
    failed = backup(ok=False, at="2026-11-05T03:00:00Z", lastOkAt="2026-11-04T03:00:00Z", error="pg_dump: нет связи")
    assert failed["status"] == "error" and "pg_dump: нет связи" in failed["summary"]


def test_schema_behind_the_code_is_an_error(app, monkeypatch):
    monkeypatch.setattr(system_health._alembic_head, "value", "0099_future", raising=False)
    with app.app_context():
        db.session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        db.session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0008_user_capabilities')"))
        db.session.commit()
    card = _items(_overview(app))["app"]
    assert card["status"] == "error" and "0099_future" in card["summary"]


def test_calendar_for_next_year_is_asked_for_in_autumn(app):
    assert _items(_overview(app, datetime(2027, 10, 2)))["calendar"]["status"] == "warn"
    assert "2028" in _items(_overview(app, datetime(2027, 10, 2)))["calendar"]["summary"]
    assert _items(_overview(app, datetime(2027, 5, 2)))["calendar"]["status"] == "ok"


def test_alembic_head_matches_the_latest_migration(app):
    with app.app_context():
        system_health._alembic_head.value = None
        assert system_health._alembic_head() == "0008_user_capabilities"
