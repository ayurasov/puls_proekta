"""0.7.3: резервные копии — расписание и хранение в «Пульсе», копии делает контейнер backup (ADR-0015)."""

from datetime import datetime

from app.extensions import db
from app.models import AppSetting, AuditLog, User
from app.services import backups, system_health
from tests.conftest import new_client

NOW = datetime(2026, 11, 6, 9, 0, 0)  # пятница, 12:00 по Москве


def _status(app, **value):
    with app.app_context():
        row = db.session.get(AppSetting, "backup_status") or AppSetting(key="backup_status")
        row.value = value
        db.session.add(row)
        db.session.commit()


def _card(app, now=NOW):
    with app.test_request_context():
        return next(i for i in system_health.overview(now)["items"] if i["key"] == "backups")


SERVICE = {"enabled": True, "time": "03:00", "days": [1, 2, 3, 4, 5, 6, 7], "retentionDays": 30, "keepManualDays": 90,
           "source": "settings", "tz": "Europe/Moscow", "utcOffset": "+0300"}


def _grant(admin, app, login_name, *caps):
    with app.app_context():
        user_id = db.session.query(User).filter_by(login=login_name).one().id
    assert admin.patch(f"/api/users/{user_id}", json={"capabilities": list(caps)}).status_code == 200


def test_admin_sets_schedule_and_retention_with_audit(app, admin):
    body = admin.get("/api/system/backups").get_json()
    assert body["saved"] is False and body["service"]["alive"] is False and body["canManage"] is True
    assert body["settings"] == backups.DEFAULTS
    for bad, text in (({"time": "25:00"}, "ЧЧ:ММ"), ({"days": [8]}, "от 1"), ({"days": []}, "хотя бы один день"),
                      ({"retentionDays": 0}, None)):
        response = admin.put("/api/system/backups/settings", json={**backups.DEFAULTS, **bad})
        assert response.status_code == 400, bad
        if text:
            assert text in response.get_json()["message"]
    saved = admin.put("/api/system/backups/settings", json={"enabled": True, "time": "02:30", "days": [5, 1, 1, 2, 3, 4],
                                                             "retentionDays": 14, "keepManualDays": 0}).get_json()
    assert saved["saved"] is True
    assert saved["settings"] == {"enabled": True, "time": "02:30", "days": [1, 2, 3, 4, 5], "retentionDays": 14,
                                 "keepManualDays": 0}
    with app.app_context():
        entry = db.session.query(AuditLog).filter_by(field="backup").one()
        assert entry.old_value == "из настроек сервера"
        assert entry.new_value == "по будням в 02:30; хранить 14 дн., вручную — всегда"
    # Выключить копии по расписанию можно и без дней
    off = admin.put("/api/system/backups/settings", json={**backups.DEFAULTS, "enabled": False, "days": []})
    assert off.status_code == 200


def test_copy_on_request_and_who_can_do_what(app, admin):
    watcher = new_client(app, "watcher", "editor")
    keeper = new_client(app, "keeper", "editor")
    assert watcher.get("/api/system/backups").status_code == 403
    _grant(admin, app, "watcher", "system.health")
    _grant(admin, app, "keeper", "backups.manage")
    view = watcher.get("/api/system/backups").get_json()
    assert view["canManage"] is False
    assert watcher.post("/api/system/backups/run").status_code == 403
    assert watcher.put("/api/system/backups/settings", json=backups.DEFAULTS).status_code == 403
    requested = keeper.post("/api/system/backups/run")
    assert requested.status_code == 202
    assert requested.get_json()["request"]["by"] == "Keeper"
    again = keeper.post("/api/system/backups/run")
    assert again.status_code == 409 and "уже запрошена" in again.get_json()["message"]
    # Служба сняла запрос и делает копию — новый запрос не нужен
    with app.app_context():
        db.session.delete(db.session.get(AppSetting, "backup_request"))
        db.session.commit()
    _status(app, running={"label": "manual", "startedAt": "2026-11-06T08:59:30Z"})
    busy = keeper.post("/api/system/backups/run")
    assert busy.status_code == 409 and "уже делается" in busy.get_json()["message"]


def test_overview_reports_service_files_and_next_copy(app, admin):
    _status(app, ok=True, at="2026-11-06T00:00:10Z", lastOkAt="2026-11-06T00:00:10Z", label="daily",
            file="puls_20261106-030000_daily.dump", size=5_000_000, lastDailyDate="2026-11-06",
            seenAt="2026-11-06T08:55:00Z", service=SERVICE, count=2, totalBytes=9_000_000,
            files=[{"name": "puls_20261106-030000_daily.dump", "size": 5_000_000, "at": "2026-11-06T00:00:10Z"},
                   {"name": "puls_20261103-120000_pre-update.dump", "size": 4_000_000, "at": "2026-11-03T09:00:00Z"}])
    with app.test_request_context():
        body = backups.overview(NOW)
    assert body["service"]["alive"] is True and body["service"]["scheduleText"] == "ежедневно в 03:00"
    assert [f["kind"] for f in body["files"]] == ["По расписанию", "Перед обновлением"]
    assert body["nextAt"] == "2026-11-07T00:00:00Z"  # завтра в 03:00 по Москве
    assert body["nextLocal"] == "07.11.2026, 03:00"
    card = _card(app)
    assert card["status"] == "ok" and card["summary"] == "Последняя копия 8 ч назад, 4,8 МБ"
    assert card["details"][0] == "Следующая копия: 07.11 в 03:00"
    assert card["link"] == "/admin/backups"


def test_health_card_knows_the_schedule_and_the_service(app):
    base = dict(ok=True, at="2026-11-06T00:00:10Z", lastOkAt="2026-11-06T00:00:10Z", seenAt="2026-11-06T08:58:00Z",
                lastDailyDate="2026-11-06", service=SERVICE)
    _status(app, **{**base, "seenAt": "2026-11-06T07:00:00Z"})
    stale = _card(app)
    assert stale["status"] == "error" and "не отвечает" in stale["summary"]
    _status(app, **{**base, "service": {**SERVICE, "enabled": False}})
    assert _card(app)["status"] == "warn" and "выключены" in _card(app)["summary"]
    # По будням: в понедельник утром копия пятницы — не опоздание, во вторник — уже да
    weekdays = {**SERVICE, "days": [1, 2, 3, 4, 5]}
    friday = {**base, "service": weekdays, "lastOkAt": "2026-11-06T00:00:10Z"}
    _status(app, **{**friday, "seenAt": "2026-11-09T00:30:00Z"})
    assert _card(app, datetime(2026, 11, 9, 0, 40))["status"] == "ok"
    _status(app, **{**friday, "seenAt": "2026-11-10T03:00:00Z"})
    assert _card(app, datetime(2026, 11, 10, 3, 10))["status"] in ("warn", "error")
    # Нет сведений — «не настроено», со ссылкой на раздел
    with app.app_context():
        db.session.delete(db.session.get(AppSetting, "backup_status"))
        db.session.commit()
    empty = _card(app)
    assert empty["status"] == "off" and empty["summary"] == "Резервное копирование не настроено"


def test_schedule_helpers():
    assert backups.max_gap_days([1, 2, 3, 4, 5, 6, 7]) == 1
    assert backups.max_gap_days([1, 2, 3, 4, 5]) == 3
    assert backups.max_gap_days([3]) == 7
    assert backups.max_gap_days([]) is None
    assert backups.schedule_text({"enabled": True, "time": "01:15", "days": [2, 4]}) == "вт, чт в 01:15"
    assert backups.schedule_text({"enabled": False}) == "по расписанию — выключены"
    # Сегодняшняя копия ещё не сделана, а время прошло — служба догонит её в течение минуты
    soon = backups.next_run(SERVICE, NOW, done_today="2026-11-05")
    assert soon == datetime(2026, 11, 6, 9, 1)
    assert backups.next_run({**SERVICE, "days": [1]}, NOW, "2026-11-06") == datetime(2026, 11, 9, 0, 0)
    assert backups.next_run({**SERVICE, "enabled": False}, NOW) is None
    assert backups.offset("-0130").total_seconds() == -5400 and backups.offset("bad").total_seconds() == 0
