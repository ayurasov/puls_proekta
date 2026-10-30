"""scripts/org_transfer.py — те же импорт и экспорт из командной строки (в том числе AD по расписанию)."""

import io
import json
import zipfile

from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Department, User
from app.services import ad_sync
from scripts.org_transfer import run
from tests.conftest import make_user
from tests.test_ad_sync import DIRECTORY, _configure, _directory


def _dep(admin, name, parent=None, kind="department"):
    res = admin.post("/api/departments", json={"name": name, "parentId": parent, "kind": kind})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


def test_cli_exports_and_imports_dry_run_by_default(app, admin, tmp_path):
    company = _dep(admin, "Компания", kind="company")
    _dep(admin, "Склад", company)
    worker = make_user(app, "worker", "editor", name="Работник", email="worker@example.test")
    admin.patch(f"/api/users/{worker}", json={"departmentId": company})

    structure = tmp_path / "structure.json"
    assert run(app, ["export-structure", "-o", str(structure)]) == 0
    bundle = json.loads(structure.read_text(encoding="utf-8"))
    assert [d["name"] for d in bundle["departments"]] == ["Компания", "Склад"]
    employees = tmp_path / "employees.zip"
    assert run(app, ["export-employees", "--format", "zip", "-o", str(employees)]) == 0
    assert "employees.json" in zipfile.ZipFile(employees).namelist()
    only_email = tmp_path / "employees.json"
    assert run(app, ["export-employees", "--format", "json", "--with-email-only", "-o", str(only_email)]) == 0
    assert [e["login"] for e in json.loads(only_email.read_text())["employees"]] == ["worker"]

    bundle["departments"].append({"key": "new", "name": "Офис", "parent": bundle["departments"][0]["key"]})
    structure.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    out = io.StringIO()
    assert run(app, ["import-structure", str(structure), "--actor", "admin"], out=out) == 0
    assert json.loads(out.getvalue().splitlines()[0])["create"] == 1
    with app.app_context():
        assert db.session.query(Department).filter_by(name="Офис").count() == 0   # без --apply ничего не пишет
    assert run(app, ["import-structure", str(structure), "--actor", "admin", "--apply"], out=io.StringIO()) == 0
    with app.app_context():
        assert db.session.query(Department).filter_by(name="Офис").count() == 1
        entry = db.session.execute(select(AuditLog).where(AuditLog.action == "import")).scalar_one()
        assert entry.user_name == "admin" and entry.entity == "Командная строка: import-structure"

    people = tmp_path / "people.csv"
    people.write_bytes("email;ФИО;Подразделение\nnew@example.test;Новый Сотрудник;Компания / Офис\n".encode("cp1251"))
    assert run(app, ["import-employees", str(people), "--actor", "admin", "--apply"], out=io.StringIO()) == 0
    with app.app_context():
        new = db.session.execute(select(User).where(User.email == "new@example.test")).scalar_one()
        assert new.name == "Новый Сотрудник" and not new.is_active and new.password_hash is None
    assert run(app, ["import-employees", str(employees), "--actor", "admin"], out=io.StringIO()) == 0

    quick = tmp_path / "quick.csv"
    quick.write_text("email;ФИО;department\nghost@example.test;Призрак;Компания\n", encoding="utf-8")
    out = io.StringIO()
    assert run(app, ["import-csv", str(quick), "--actor", "admin", "--apply"], out=out) == 1
    assert "пользователь не найден" in out.getvalue()
    assert run(app, ["import-csv", str(quick), "--actor", "admin", "--create-missing", "--apply"], out=io.StringIO()) == 0

    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")
    assert run(app, ["import-structure", str(broken), "--actor", "admin"]) == 2
    try:
        run(app, ["import-structure", str(structure), "--actor", "worker"])
    except SystemExit as error:
        assert "администратора" in str(error)
    else:
        raise AssertionError("не администратор — отказ")


def test_cli_ad_sync_for_a_schedule(app, admin, monkeypatch):
    _configure(admin)
    monkeypatch.setattr(ad_sync, "connect", lambda config: _directory(DIRECTORY))
    out = io.StringIO()
    assert run(app, ["sync-ad", "--actor", "admin", "--create-missing", "--apply"], out=out) == 0
    assert json.loads(out.getvalue().splitlines()[0])["createUsers"] == 5
    with app.app_context():
        assert db.session.execute(select(User).where(User.email == "newbie@example.com")).scalar_one().org_source == "ad"
    _configure(admin, url="ldap://127.0.0.1:9")
    monkeypatch.undo()
    assert run(app, ["sync-ad", "--actor", "admin"]) == 2
