from app.extensions import db
from app.models import AuditLog, User
from tests.conftest import login, make_user, new_client


def _id(app, login_name):
    with app.app_context():
        return db.session.query(User).filter_by(login=login_name).one().id


def test_only_admin_manages_users(app, admin):
    editor = new_client(app, "ed", "editor")
    assert editor.get("/api/users").status_code == 403
    assert editor.get("/api/users/directory").status_code == 200
    response = admin.post("/api/users", json={"login": "new.user", "name": "  Новый  Сотрудник ",
                                              "password": "password-1", "role": "editor"})
    assert response.status_code == 201
    assert response.get_json()["name"] == "Новый Сотрудник"
    assert admin.post("/api/users", json={"login": "NEW.USER", "name": "X", "password": "password-1"}).status_code == 409


def test_admin_cannot_lock_himself_out(app, admin):
    me = _id(app, "admin")
    assert admin.patch(f"/api/users/{me}", json={"isActive": False}).status_code == 400
    assert admin.patch(f"/api/users/{me}", json={"role": "viewer"}).status_code == 400
    assert admin.delete(f"/api/users/{me}").status_code == 400


def test_last_active_admin_is_protected(app, admin):
    make_user(app, "admin2", "admin")
    other = _id(app, "admin2")
    assert admin.patch(f"/api/users/{other}", json={"role": "viewer"}).status_code == 200
    # admin2 теперь не администратор; снова повысим и попробуем отключить последнего
    assert admin.patch(f"/api/users/{other}", json={"role": "admin"}).status_code == 200
    second = app.test_client()
    login(second, "admin2")
    me = _id(app, "admin")
    assert second.patch(f"/api/users/{me}", json={"isActive": False}).status_code == 200
    assert second.patch(f"/api/users/{other}", json={"isActive": False}).status_code == 400


def test_user_changes_are_audited_with_old_and_new_values(app, admin):
    make_user(app, "x", "viewer", name="Икс")
    uid = _id(app, "x")
    admin.patch(f"/api/users/{uid}", json={"role": "editor", "position": "Аналитик"})
    with app.app_context():
        rows = db.session.query(AuditLog).filter(AuditLog.field.in_(["role", "position"])).all()
        values = {(r.field, r.old_value, r.new_value) for r in rows}
    assert ("role", "Наблюдатель", "Редактор") in values
    assert ("position", "", "Аналитик") in values


def test_avatar_upload_and_cached_download(app, admin):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    response = admin.put("/api/profile/avatar", data={"file": (__import__("io").BytesIO(png), "a.png")},
                         content_type="multipart/form-data")
    assert response.status_code == 200
    url = response.get_json()["user"]["avatarUrl"]
    assert url.startswith("/api/users/") and "?v=1" in url
    image = admin.get(url)
    assert image.status_code == 200 and image.data == png and image.mimetype == "image/png"
    etag = image.headers["ETag"].strip('"')
    assert admin.get(url, headers={"If-None-Match": f'"{etag}"'}).status_code == 304
    bad = admin.put("/api/profile/avatar", json={"dataUrl": "data:text/html;base64,PHNjcmlwdD4="})
    assert bad.status_code == 400


def test_audit_is_shown_in_plain_language_for_new_and_legacy_rows(app, admin):
    """Реестр показывает ФИО, подписи разделов/полей и значения по-русски; записи 1.0.x не переписываются."""
    from app.models import Project

    make_user(app, "karl", "viewer", name="Карлов Карл")
    uid = _id(app, "karl")
    admin.patch(f"/api/users/{uid}", json={"role": "editor"})
    admin.put("/api/calendar/2026-05-04", json={"kind": "holiday", "title": "Перенос"})
    with app.app_context():
        project = Project(title="Старый проект", position=1)
        db.session.add(project)
        db.session.flush()
        legacy = [
            AuditLog(user_id=None, user_name="karl", section="Гант", action="update", entity=f"project #{project.id}",
                     field="data", old_value="", new_value="(данные обновлены)"),
            AuditLog(user_id=uid, user_name="admin", section="Пользователи", action="update",
                     entity=f"user #{uid} (Карлов Карл)", field="active", old_value="", new_value="true"),
            AuditLog(user_id=uid, user_name="admin", section="Пользователи", action="update",
                     entity=f"user #{uid} (Карлов Карл)", field="avatar", old_value="",
                     new_value="data:image/jpeg;base64,AAAA"),
            AuditLog(user_id=uid, user_name="admin", section="Пользователи", action="update",
                     entity=f"user #{uid} (Карлов Карл)", field="role", old_value="", new_value="viewer"),
        ]
        db.session.add_all(legacy)
        db.session.commit()
        legacy_ids = [row.id for row in legacy]
    rows = {r["id"]: r for r in admin.get("/api/audit?limit=500").get_json()["rows"]}
    data_row, active_row, avatar_row, role_row = (rows[i] for i in legacy_ids)
    assert data_row["sectionLabel"] == "Планы" and data_row["section"] == "Гант"
    assert data_row["entityLabel"] == "Проект «Старый проект»" and data_row["userName"] == "Карлов Карл"
    assert data_row["userLogin"] == "karl" and data_row["fieldLabel"] == "Данные плана"
    assert active_row["sectionLabel"] == "Сотрудники" and active_row["entityLabel"] == "Сотрудник Карлов Карл"
    assert active_row["fieldLabel"] == "Активен" and active_row["newLabel"] == "активен"
    assert avatar_row["fieldLabel"] == "Фото" and avatar_row["newLabel"] == "фото обновлено"
    assert role_row["newLabel"] == "Наблюдатель"
    calendar = next(r for r in rows.values() if r["entity"].startswith("Календарь"))
    assert calendar["entityLabel"] == "Производственный календарь, 04.05.2026"
    assert calendar["fieldLabel"] == "Тип дня" and calendar["newLabel"] == "Нерабочий день: Перенос"
    role_change = next(r for r in rows.values() if r["field"] == "role" and r["oldValue"] == "Наблюдатель")
    assert role_change["userName"] == "Администратор" and role_change["fieldLabel"] == "Роль"
    # Хранимые значения не тронуты — фильтр по логину и разделу 1.0.x работает как раньше
    with app.app_context():
        assert db.session.get(AuditLog, legacy_ids[1]).new_value == "true"
    facets = admin.get("/api/audit/facets").get_json()
    assert facets["sectionLabels"]["Гант"] == "Планы" and facets["userLabels"]["karl"] == "Карлов Карл"
    assert admin.get("/api/audit?section=Гант").get_json()["total"] == 1
    csv_text = admin.get("/api/audit/export.csv").get_data(as_text=True)
    assert "Проект «Старый проект»" in csv_text and "фото обновлено" in csv_text
