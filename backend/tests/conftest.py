import os

import pytest
from sqlalchemy import text

from app import create_app
from app.cli import seed_calendar
from app.extensions import db
from app.models import User
from app.passwords import hash_password

PASSWORD = "password-123"


@pytest.fixture
def app(tmp_path):
    # TEST_DATABASE_URL=postgresql+psycopg2://… — прогон тех же тестов на PostgreSQL (CI)
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": url})
    with app.app_context():
        db.drop_all()
        db.create_all()
        seed_calendar()
    yield app
    with app.app_context():
        db.session.remove()
        # Подразделение-родитель защищено от удаления (RESTRICT): SQLite проверяет это и при DROP TABLE
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE departments SET parent_id = NULL"))
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(app, login, role="viewer", password=PASSWORD, name=None, active=True, **extra):
    with app.app_context():
        user = User(login=login, name=name or login.title(), role=role, password_hash=hash_password(password),
                    is_active=active, **extra)
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, login_name, password=PASSWORD):
    response = client.post("/api/auth/login", json={"login": login_name, "password": password})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["user"]


@pytest.fixture
def admin(app, client):
    make_user(app, "admin", "admin", name="Администратор")
    login(client, "admin")
    return client


def new_client(app, login_name, role="editor", **kwargs):
    make_user(app, login_name, role, **kwargs)
    c = app.test_client()
    login(c, login_name)
    return c
