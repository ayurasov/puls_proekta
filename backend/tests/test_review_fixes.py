"""Регрессионные тесты по итогам ревизии сервера 0.2.0 (docs/review/v0.2.0-revision-1.0.1.md)."""

import math
from datetime import timedelta

from sqlalchemy import func, select

from tests.conftest import login, make_user
from tests.helpers import create_project, ok_ops, ops, task
from app.extensions import db
from app.ldap_auth import DirectoryResult
from app.models import AccessRule, AppSetting, User, UserSession, utcnow

LEGACY = "../legacy/snapshot/puls-2026-10-30.db"


def _enable_ldap(app, mode):
    with app.app_context():
        db.session.add(AppSetting(key="ldap", value={
            "enabled": True, "authMode": mode, "serverUrl": "ldap://dc", "baseDN": "dc=x", "bindDN": "",
            "bindPassword": "", "searchFilter": "(uid={{username}})",
            "userAttributes": {"username": "uid", "name": "cn", "email": "mail"}, "defaultRole": "viewer",
            "timeoutSeconds": 5,
        }))
        db.session.commit()


def test_directory_account_never_takes_over_local_admin(app, client, monkeypatch):
    """Учётная запись каталога «admin» не получает права локального администратора и не стирает его пароль."""
    make_user(app, "admin", "admin", name="Администратор")
    import app.api.auth as auth_api
    monkeypatch.setattr(auth_api, "ldap_authenticate",
                        lambda username, password, config: DirectoryResult(ok=True, username="admin", name="Чужой"))
    for mode in ("ldap", "hybrid"):
        with app.app_context():
            db.session.query(AppSetting).delete()
            db.session.commit()
        _enable_ldap(app, mode)
        res = client.post("/api/auth/login", json={"login": "admin", "password": "пароль каталога"})
        assert res.status_code == 401, mode
        login(client, "admin")  # пароль системы по-прежнему работает
        client.post("/api/auth/logout")
    with app.app_context():
        admin = db.session.execute(select(User).where(User.login == "admin")).scalar_one()
        assert admin.auth_source == "local" and admin.password_hash and admin.name == "Администратор"


def test_long_titles_and_settings_fit_audit_columns(app, admin):
    """Длинные названия и много изменённых настроек — без ошибки 500 (в PostgreSQL длины столбцов строгие)."""
    project_id, plan_id = create_project(admin, "П" * 495)
    assert admin.delete(f"/api/projects/{project_id}").status_code == 200
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", category="к" * 400)])
    item = admin.get(f"/api/plans/{plan_id}").get_json()["items"][0]
    assert len(item["category"]) == 255
    res = admin.put("/api/settings/ldap", json={
        "enabled": True, "authMode": "hybrid", "serverUrl": "ldaps://dc.example.ru:636",
        "baseDN": "dc=example,dc=ru", "bindDN": "cn=svc,dc=example,dc=ru", "bindPassword": "secret",
        "searchFilter": "(sAMAccountName={{username}})",
        "userAttributes": {"username": "sAMAccountName", "name": "displayName", "email": "mail"},
        "defaultRole": "editor", "timeoutSeconds": 7,
    })
    assert res.status_code == 200


def test_bad_input_is_rejected_not_500(app, admin):
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [{"op": "item.create", "item": {"key": "g1", "type": "group", "name": "Группа"}}])
    assert ops(admin, plan_id, [{"op": "item.update", "key": "g1", "fields": {"start": "2026-05-01"}}]).status_code in (200, 400)
    for user_id in ("abc", 999999):
        res = ops(admin, plan_id, [{"op": "assignee.create", "assignee": {"key": "a1", "name": "Исп", "userId": user_id}}])
        assert res.status_code == 400, user_id
    for value in (math.nan, math.inf):
        assert ops(admin, plan_id, [task("t9", progress=value)]).status_code == 400
        assert ops(admin, plan_id, [task("t9", position=value)]).status_code == 400
    assert admin.get("/api/audit?from=2026-13-45").status_code == 400


def test_project_position_must_be_finite(app, admin):
    project_id, _ = create_project(admin)
    res = admin.post("/api/projects/reorder", data='{"items": [{"id": %d, "position": NaN}]}' % project_id,
                     content_type="application/json")
    assert res.status_code == 400
    assert "NaN" not in admin.get("/api/projects").get_data(as_text=True)


def test_bulk_grant_ignores_duplicate_ids(app, admin):
    project_id, _ = create_project(admin)
    user_id = make_user(app, "ivanov", "editor")
    res = admin.post("/api/access", json={"userIds": [user_id, user_id], "projectIds": [project_id, project_id],
                                          "level": "edit"})
    assert res.status_code in (200, 201)
    with app.app_context():
        assert db.session.execute(select(func.count()).select_from(AccessRule)
                                  .where(AccessRule.user_id == user_id)).scalar_one() == 1


def test_copy_keeps_legacy_fields(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [{"op": "item.create", "item": {
        "key": "t1", "type": "task", "name": "Задача", "start": "2026-05-01", "end": "2026-05-02",
        "extra": {"legacyGroupId": "grp7"}}}])
    copy = admin.post(f"/api/projects/{project_id}/plans", json={"sourcePlanId": plan_id}).get_json()
    item = admin.get(f"/api/plans/{copy['id']}").get_json()["items"][0]
    assert item["extra"] == {"legacyGroupId": "grp7"}


def test_anonymous_sessions_are_short_lived(app, client):
    client.get("/api/auth/csrf")
    with app.app_context():
        row = db.session.execute(select(UserSession).where(UserSession.user_id.is_(None))).scalar_one()
        assert row.expires_at - utcnow() <= timedelta(days=1)
    make_user(app, "petrov", "editor")
    login(client, "petrov")
    with app.app_context():
        row = db.session.execute(select(UserSession).where(UserSession.user_id.is_not(None))).scalar_one()
        assert row.expires_at - utcnow() > timedelta(days=20)


def test_rehearsal_rollback_keeps_sequences_ahead(app, client):
    """Репетиция переноса (--dry-run --replace) на рабочей базе откатывается, а счётчики PostgreSQL — нет:
    они не должны уйти ниже уже занятых id (иначе каждая следующая запись — ошибка 500)."""
    from app.passwords import hash_password
    from app.models import Plan
    from app.services.legacy_import import LegacyImporter, _wipe_target, import_legacy_database, open_legacy
    with app.app_context():
        _stats, _report, errors = import_legacy_database(LEGACY)
        assert not errors
        admin = db.session.execute(select(User).where(User.role == "admin", User.is_active.is_(True))).scalars().first()
        admin.password_hash, admin.auth_source = hash_password("test-password-1"), "local"
        db.session.commit()
        login_name = admin.login
        plan_id = db.session.execute(select(Plan.id).where(Plan.deleted_at.is_(None))).scalars().first()
    login(client, login_name, "test-password-1")
    for i in range(3):  # записи с id выше перенесённых
        ok_ops(client, plan_id, [task(f"new{i}")])
    with app.app_context():  # как scripts/import_legacy.py --dry-run --replace
        conn, _tmp = open_legacy(LEGACY)
        try:
            _wipe_target()
            LegacyImporter(conn).run()
        finally:
            db.session.rollback()
            conn.close()
    assert ops(client, plan_id, [task("after")]).status_code == 200
