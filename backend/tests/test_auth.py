from app.extensions import db
from app.models import User, UserSession
from tests.conftest import PASSWORD, login, make_user


def test_login_me_logout(app, client):
    make_user(app, "ivanov", "editor", name="Иванов Иван")
    user = login(client, "ivanov")
    assert user["name"] == "Иванов Иван"
    assert "password" not in user and "passwordHash" not in user
    me = client.get("/api/auth/me").get_json()["user"]
    assert me["login"] == "ivanov"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_login_is_case_insensitive_and_accepts_email(app, client):
    make_user(app, "petrov.pp", "viewer", email="petrov@example.com")
    assert login(client, "Petrov.PP")["login"] == "petrov.pp"
    client.post("/api/auth/logout")
    assert login(client, "PETROV@example.com")["login"] == "petrov.pp"


def test_wrong_password_and_disabled_account(app, client):
    make_user(app, "sidorov")
    response = client.post("/api/auth/login", json={"login": "sidorov", "password": "nope"})
    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid_credentials"
    make_user(app, "off", active=False)
    response = client.post("/api/auth/login", json={"login": "off", "password": PASSWORD})
    assert response.status_code == 403


def test_legacy_bcrypt_hash_from_1_0_x_is_accepted(app, client):
    # Хэш bcryptjs ($2a$, cost 12) пароля «legacy-pass» — так пароли хранились в 1.0.x
    import bcrypt

    legacy_hash = bcrypt.hashpw(b"legacy-pass", bcrypt.gensalt(rounds=4, prefix=b"2a")).decode()
    with app.app_context():
        db.session.add(User(login="old", name="Old", role="viewer", password_hash=legacy_hash))
        db.session.commit()
    login(client, "old", "legacy-pass")


def test_session_rotates_on_login_and_is_stored_hashed(app, client):
    make_user(app, "user1")
    client.get("/api/auth/csrf")
    before = client.get_cookie("puls_session")
    login(client, "user1")
    after = client.get_cookie("puls_session")
    assert before is not None and after is not None and before.value != after.value
    with app.app_context():
        ids = [s.id for s in db.session.query(UserSession).all()]
    assert after.value not in ids  # в БД только хэш токена


def test_deactivation_revokes_sessions_immediately(app, admin):
    make_user(app, "worker", "editor")
    worker = app.test_client()
    login(worker, "worker")
    assert worker.get("/api/auth/me").status_code == 200
    with app.app_context():
        uid = db.session.query(User).filter_by(login="worker").one().id
    assert admin.patch(f"/api/users/{uid}", json={"isActive": False}).status_code == 200
    assert worker.get("/api/auth/me").status_code == 401


def test_change_password_logs_out_other_devices(app, client):
    make_user(app, "multi")
    other = app.test_client()
    login(client, "multi")
    login(other, "multi")
    response = client.put("/api/profile/password", json={"currentPassword": PASSWORD, "newPassword": "short"})
    assert response.status_code == 400
    response = client.put("/api/profile/password", json={"currentPassword": PASSWORD, "newPassword": "new-password-1"})
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    client.post("/api/auth/logout")
    login(client, "multi", "new-password-1")


def test_csrf_required_when_enabled(app):
    app.config["WTF_CSRF_ENABLED"] = True
    make_user(app, "csrfuser")
    c = app.test_client()
    response = c.post("/api/auth/login", json={"login": "csrfuser", "password": PASSWORD})
    assert response.status_code == 400 and response.get_json()["error"] == "csrf_error"
    token = c.get("/api/auth/csrf").get_json()["csrfToken"]
    response = c.post("/api/auth/login", json={"login": "csrfuser", "password": PASSWORD},
                      headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    # Токен переживает вход: следующий изменяющий запрос с тем же токеном проходит
    response = c.post("/api/auth/logout", headers={"X-CSRF-Token": token})
    assert response.status_code == 200
