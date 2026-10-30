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
