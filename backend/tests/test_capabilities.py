"""0.7.2: полномочия без роли администратора (ADR-0013)."""

from app.capabilities import CAPABILITIES
from app.extensions import db
from app.models import AuditLog, User
from tests.conftest import make_user, new_client


def _user_id(app, login_name):
    with app.app_context():
        return db.session.query(User).filter_by(login=login_name).one().id


def _grant(admin, app, login_name, *caps):
    response = admin.patch(f"/api/users/{_user_id(app, login_name)}", json={"capabilities": list(caps)})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_admin_grants_capabilities_and_they_are_audited(app, admin):
    make_user(app, "hr", "editor", name="Кадровик")
    body = _grant(admin, app, "hr", "org.manage", "system.health", "system.health")
    # Порядок — как в списке у администратора, повторы убираются
    assert body["capabilities"] == ["system.health", "org.manage"]
    me = new_client(app, "watcher", "viewer")
    assert me.get("/api/auth/me").get_json()["user"]["capabilities"] == []
    unknown = admin.patch(f"/api/users/{_user_id(app, 'hr')}", json={"capabilities": ["root"]})
    assert unknown.status_code == 400 and "root" in unknown.get_json()["message"]
    with app.app_context():
        entry = db.session.query(AuditLog).filter_by(field="capabilities").one()
        assert entry.old_value == "нет"
        assert entry.new_value == "Смотрит состояние системы, Ведёт структуру компании"
    assert list(CAPABILITIES)[0] == "system.health"


def test_without_capability_sections_are_closed_with_it_open(app, admin):
    sections = {
        "system.health": ("GET", "/api/system/health"),
        "modules.manage": ("GET", "/api/settings/jira"),
        "calendar.manage": ("PUT", "/api/calendar/2027-03-10"),
        "audit.view": ("GET", "/api/audit"),
        "users.manage": ("GET", "/api/users/export?format=json"),
        "org.manage": ("GET", "/api/org/export"),
        "access.manage": ("GET", "/api/access"),
        "trash.manage": ("POST", "/api/projects/999/restore"),
    }
    for i, (cap, (method, path)) in enumerate(sections.items()):
        login_name = f"user{i}"
        client = new_client(app, login_name, "editor")
        call = getattr(client, method.lower())
        kwargs = {"json": {"kind": "holiday", "title": "Праздник"}} if method == "PUT" else {}
        assert call(path, **kwargs).status_code == 403, cap
        _grant(admin, app, login_name, cap)
        assert call(path, **kwargs).status_code in (200, 201, 404), (cap, call(path, **kwargs).get_json())
    # Ключи доступа и вход через домен — только администратору, даже с «Настраивает модули»
    modules = new_client(app, "modules", "editor")
    _grant(admin, app, "modules", "modules.manage")
    assert modules.get("/api/settings/integration-keys").status_code == 403
    assert modules.get("/api/settings/ldap").status_code == 403
    assert modules.post("/api/settings/podelu/links/key").status_code == 403
    assert modules.get("/api/settings/podelu/links").status_code == 200


def test_users_manager_cannot_touch_admins_or_grant_admin(app, admin):
    support = new_client(app, "support", "editor")
    _grant(admin, app, "support", "users.manage")
    make_user(app, "boss", "admin", name="Второй администратор")
    make_user(app, "plain", "editor", name="Сотрудник")
    boss, plain = _user_id(app, "boss"), _user_id(app, "plain")

    created = support.post("/api/users", json={"login": "new.one", "name": "Новый", "password": "Very-long-pass-1"})
    assert created.status_code == 201
    assert support.post("/api/users", json={"login": "x", "name": "X", "password": "Very-long-pass-1",
                                            "role": "admin"}).status_code == 403
    assert support.patch(f"/api/users/{plain}", json={"role": "manager", "name": "Сотрудник Иванов"}).status_code == 200
    assert support.patch(f"/api/users/{plain}", json={"role": "admin"}).status_code == 403
    assert support.patch(f"/api/users/{plain}", json={"capabilities": ["audit.view"]}).status_code == 403
    denied = support.patch(f"/api/users/{boss}", json={"password": "Another-long-pass-2"})
    assert denied.status_code == 403 and "администратора" in denied.get_json()["message"]
    assert support.delete(f"/api/users/{boss}").status_code == 403
    # Своя роль и включение — у администратора
    me = _user_id(app, "support")
    assert support.patch(f"/api/users/{me}", json={"role": "manager"}).status_code == 400
    # Сотрудник с полномочиями, которых нет у «Ведёт учётные записи», защищён: иначе через его пароль
    # можно было бы получить его права
    make_user(app, "auditor", "editor")
    _grant(admin, app, "auditor", "audit.view")
    guarded = support.patch(f"/api/users/{_user_id(app, 'auditor')}", json={"password": "Another-long-pass-2"})
    assert guarded.status_code == 403 and "полномочия" in guarded.get_json()["message"]
    # Список сотрудников открыт и «Ведёт учётные записи»
    assert support.get("/api/users").status_code == 200


def test_org_manager_changes_only_structure_fields(app, admin):
    hr = new_client(app, "hr", "editor")
    _grant(admin, app, "hr", "org.manage")
    make_user(app, "plain", "editor")
    plain = _user_id(app, "plain")
    dep = hr.post("/api/departments", json={"name": "Отдел продаж"})
    assert dep.status_code == 201, dep.get_json()
    moved = hr.patch(f"/api/users/{plain}", json={"departmentId": dep.get_json()["id"], "position": "Менеджер"})
    assert moved.status_code == 200
    denied = hr.patch(f"/api/users/{plain}", json={"email": "x@example.com"})
    assert denied.status_code == 403 and "структуру" in denied.get_json()["message"]
    # Синхронизация с AD и «По Делу» меняет учётные записи — нужны оба полномочия
    sync = hr.post("/api/org/sync/ad", json={})
    assert sync.status_code == 403 and "Ведёт учётные записи" in sync.get_json()["message"]


def test_trash_manager_sees_every_project_and_purges(app, admin):
    keeper = new_client(app, "keeper", "editor")
    project = admin.post("/api/projects", json={"title": "Удаляемый"}).get_json()
    assert admin.delete(f"/api/projects/{project['id']}").status_code == 200
    assert keeper.get("/api/trash").get_json()["projects"] == []
    _grant(admin, app, "keeper", "trash.manage")
    trash = keeper.get("/api/trash").get_json()
    assert [p["title"] for p in trash["projects"]] == ["Удаляемый"] and trash["canPurge"] is True
    assert keeper.delete(f"/api/trash/projects/{project['id']}").status_code == 200


def test_disabled_user_loses_capabilities(app, admin):
    make_user(app, "hr", "editor")
    _grant(admin, app, "hr", "org.manage")
    with app.app_context():
        from app.security import has_capability, user_capabilities

        user = db.session.query(User).filter_by(login="hr").one()
        assert has_capability(user, "org.manage") and user_capabilities(user) == ["org.manage"]
        user.is_active = False
        assert not has_capability(user, "org.manage") and user_capabilities(user) == []
        boss = db.session.query(User).filter_by(login="admin").one()
        assert user_capabilities(boss) == list(CAPABILITIES)


def test_employee_import_by_users_manager_keeps_admins(app, admin):
    support = new_client(app, "support", "editor")
    _grant(admin, app, "support", "users.manage")
    make_user(app, "boss", "admin", name="Начальник", email="boss@example.com")
    make_user(app, "plain", "editor", name="Сотрудник", email="plain@example.com")
    doc = {"format": "podelu.employees", "version": 1, "employees": [
        {"name": "Начальник", "login": "boss", "email": "boss@example.com", "position": "Новый директор", "role": "viewer"},
        {"name": "Сотрудник", "login": "plain", "email": "plain@example.com", "role": "admin"},
    ]}
    import json
    plan = support.post("/api/users/import", json={"format": "json", "content": json.dumps(doc, ensure_ascii=False)})
    assert plan.status_code == 200, plan.get_json()
    warnings = " ".join(w for e in plan.get_json()["records"] for w in e.get("warnings", []))
    assert "администратора меняет только администратор" in warnings
    assert "роль «Администратор» назначает только администратор" in warnings
