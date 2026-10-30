"""Сотрудники файлом podelu.employees (как в «По Делу», ADR-0051 там): форматы, новые сотрудники,
коллизии с решениями администратора, совпадение личности по логину, фото, защита администраторов."""

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.extensions import db
from app.models import Department, DepartmentHead, User, UserAvatar
from app.services.org import OrgSnapshot
from tests.conftest import make_user, new_client

FIXTURES = Path(__file__).parent / "fixtures" / "todo_planner"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 40
HEADER = ["email", "Логин", "ФИО", "Должность", "Подразделение", "Руководитель (явно)", "Первое лицо", "Руководит",
          "Функциональные руководители", "Роль", "Полномочия", "Часовой пояс", "Активен", "Видимость", "Вход",
          "Внешний ID"]


def _dep(admin, name, parent=None, kind="department"):
    res = admin.post("/api/departments", json={"name": name, "parentId": parent, "kind": kind})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


@pytest.fixture
def world(app, admin):
    company = _dep(admin, "Вектор", kind="company")
    sales = _dep(admin, "Продажи", company)
    head = make_user(app, "et-head", "manager", name="Анна Руководитель", email="et-head@example.test",
                     position="Начальник отдела")
    worker = make_user(app, "et-worker", "editor", name="Пётр Сотрудник", email="et-worker@example.test")
    for user_id in (head, worker):
        assert admin.patch(f"/api/users/{user_id}", json={"departmentId": sales}).status_code == 200
    assert admin.put(f"/api/departments/{sales}/heads", json={"userIds": [head]}).status_code == 200
    assert admin.put(f"/api/users/{worker}/avatar", data={"file": (io.BytesIO(PNG), "a.png")},
                     content_type="multipart/form-data").status_code == 200
    with app.app_context():
        admin_id = db.session.execute(select(User.id).where(User.login == "admin")).scalar_one()
    return {"admin": admin_id, "head": head, "worker": worker, "company": company, "sales": sales}


def _import(client, content, fmt="json", **extra):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return client.post("/api/users/import", json={"format": fmt, "content": content, **extra})


def _doc(*employees):
    return {"format": "podelu.employees", "version": 1, "employees": list(employees)}


def _records(plan):
    return {record["email"] or record["login"]: record for record in plan["records"]}


def test_export_formats_contain_no_passwords(app, admin, world):
    response = admin.get("/api/users/export?format=csv")
    assert response.status_code == 200 and response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"].startswith('attachment; filename="puls-employees-')
    raw = response.data.decode("utf-8")
    assert raw.startswith("﻿") and "\r\n" in raw
    rows = list(csv.reader(io.StringIO(raw.lstrip("﻿")), delimiter=";"))
    assert rows[0] == HEADER
    head = next(row for row in rows if row[0] == "et-head@example.test")
    assert head[1:8] == ["et-head", "Анна Руководитель", "Начальник отдела", "Вектор / Продажи", "", "нет",
                         "Вектор / Продажи (основной)"]
    assert head[8:] == ["", "Менеджер", "", "", "да", "", "local", ""]
    admin_row = next(row for row in rows if row[1] == "admin")
    assert admin_row[0] == "" and admin_row[9] == "Администратор"
    assert "password" not in raw.lower() and "$2b$" not in raw

    document = json.loads(admin.get("/api/users/export?format=json").data)
    assert document["format"] == "podelu.employees" and document["version"] == 1 and document["product"] == "puls"
    worker = next(item for item in document["employees"] if item["email"] == "et-worker@example.test")
    assert worker == {
        "email": "et-worker@example.test", "login": "et-worker", "name": "Пётр Сотрудник", "position": "",
        "department": ["Вектор", "Продажи"], "manager": "", "reportsToNone": False, "heads": [],
        "functionalManagers": [], "role": "editor", "capabilities": [], "timezone": "", "isActive": True,
        "visibilityScope": "", "authSource": "local", "externalId": "",
    }

    archive = zipfile.ZipFile(io.BytesIO(admin.get("/api/users/export?format=zip&avatars=1").data))
    names = archive.namelist()
    assert "employees.json" in names and "README.txt" in names and any(n.startswith("avatars/") for n in names)
    assert "Пароли не выгружаются" in archive.read("README.txt").decode("utf-8")
    inside = json.loads(archive.read("employees.json"))
    worker = next(item for item in inside["employees"] if item["email"] == "et-worker@example.test")
    assert worker["avatar"].endswith(".png") and archive.read(worker["avatar"]) == PNG
    plain = zipfile.ZipFile(io.BytesIO(admin.get("/api/users/export?format=zip&avatars=0").data))
    assert not any(name.startswith("avatars/") for name in plain.namelist())

    admin.patch(f"/api/users/{world['worker']}", json={"isActive": False})
    active = json.loads(admin.get("/api/users/export?format=json&inactive=0").data)
    assert "et-worker@example.test" not in {item["email"] for item in active["employees"]}
    assert admin.get("/api/users/export?format=xlsx").status_code == 400
    # Для «По Делу»: только сотрудники с email
    for_podelu = json.loads(admin.get("/api/users/export?format=json&withoutEmail=0").data)
    assert "admin" not in {item["login"] for item in for_podelu["employees"]}
    assert "admin" in {item["login"] for item in active["employees"]}


def test_new_employees_are_created_with_departments_heads_and_managers(app, admin, world):
    document = _doc(
        {"email": "Boss@Example.test", "name": "Новый Руководитель", "department": ["Вектор", "Маркетинг"],
         "heads": [{"department": ["Вектор", "Маркетинг"], "primary": True}], "role": "manager", "position": "Директор"},
        {"email": "staff@example.test", "name": "Новый Сотрудник", "department": "Вектор / Маркетинг",
         "manager": "boss@example.test"},
        {"email": "freelancer@example.test", "name": "Внештатный", "manager": "boss@example.test", "authSource": "ldap"},
    )
    plan = _import(admin, document, options={"newUsers": "inactive"}).get_json()
    assert plan["canApply"], plan
    assert plan["summary"]["create"] == 3 and [d["path"] for d in plan["departments"]] == ["Вектор / Маркетинг"]
    with app.app_context():
        assert db.session.query(User).filter_by(email="boss@example.test").count() == 0   # план ничего не пишет
    applied = _import(admin, document, options={"newUsers": "inactive"}, apply=True)
    assert applied.status_code == 200, applied.get_json()
    ids = applied.get_json()["ids"]
    with app.app_context():
        boss = db.session.get(User, ids["boss@example.test"])
        staff = db.session.get(User, ids["staff@example.test"])
        free = db.session.get(User, ids["freelancer@example.test"])
        marketing = db.session.execute(select(Department).where(Department.name == "Маркетинг")).scalar_one()
        assert marketing.org_source == "import" and marketing.parent_id == world["company"]
        assert not boss.is_active and boss.password_hash is None and boss.login == "boss" and boss.role == "manager"
        assert boss.org_source == "import" and boss.auth_source == "local" and free.auth_source == "ldap"
        assert boss.department_id == marketing.id and staff.department_id == marketing.id
        assert db.session.get(DepartmentHead, (marketing.id, boss.id)).is_primary
        # Руководитель по структуре совпадает с указанным — явным не записывается; у внештатного — явный
        assert staff.manager_id is None and free.manager_id == boss.id
        assert staff.role == "viewer"


def test_conflicts_default_to_system_and_follow_decisions(app, admin, world):
    worker_id = world["worker"]
    document = _doc({
        "email": "ET-Worker@example.test", "name": "Пётр Иванович Сотрудник", "position": "Аналитик",
        "department": ["Вектор"], "heads": [{"department": ["Вектор"], "primary": False}], "role": "viewer",
        "timezone": "Asia/Yekaterinburg",
    })
    plan = _import(admin, document).get_json()
    record = plan["records"][0]
    assert record["action"] == "update" and record["userId"] == worker_id and record["key"] == "et-worker@example.test"
    # Должность и руководство в системе пустые — заполняются без вопросов; остальное — коллизии
    assert [c["field"] for c in record["changes"]] == ["position", "heads"]
    conflicts = {c["field"]: c for c in record["conflicts"]}
    assert set(conflicts) == {"name", "department", "role"}
    assert conflicts["name"]["current"] == "Пётр Сотрудник" and conflicts["name"]["choice"] == "current"
    assert conflicts["department"]["choices"] == ["current", "incoming", "custom"]
    assert conflicts["role"]["current"] == "Редактор" and conflicts["role"]["incoming"] == "Наблюдатель"
    assert plan["notes"] == ["Поля «По Делу», которых нет в «Пульсе», не загружаются: часовой пояс"]

    decisions = {"et-worker@example.test": {"fields": {
        "name": {"choice": "custom", "value": "Пётр Иванович  Сотрудник"},
        "department": {"choice": "incoming"},
    }}}
    response = _import(admin, document, decisions=decisions, apply=True)
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        row = db.session.get(User, worker_id)
        assert row.name == "Пётр Иванович Сотрудник" and row.position == "Аналитик"
        assert row.department_id == world["company"] and row.role == "editor"   # роль оставлена как в системе
        assert db.session.get(DepartmentHead, (world["company"], worker_id)) is not None
    # Своё значение с ошибкой — ошибка записи
    bad = {"et-worker@example.test": {"fields": {"role": {"choice": "custom", "value": "директор"}}}}
    record = _import(admin, _doc({"email": "et-worker@example.test", "role": "viewer"}), decisions=bad).get_json()["records"][0]
    assert record["errors"] == ["Роль: роль: Администратор, Менеджер, Редактор или Наблюдатель"]


def test_list_fields_can_be_merged(app, admin, world):
    head_id = world["head"]
    document = _doc({"email": "et-head@example.test", "heads": [{"department": ["Вектор"], "primary": True}]})
    record = _import(admin, document).get_json()["records"][0]
    conflict = record["conflicts"][0]
    assert conflict["field"] == "heads" and conflict["choices"] == ["current", "incoming", "merge", "custom"]
    assert sorted(conflict["merged"]) == ["Вектор (основной)", "Вектор / Продажи (основной)"]
    decisions = {"et-head@example.test": {"fields": {"heads": {"choice": "merge"}}}}
    assert _import(admin, document, decisions=decisions, apply=True).status_code == 200
    with app.app_context():
        assert {row.department_id for row in db.session.execute(
            select(DepartmentHead).where(DepartmentHead.user_id == head_id)).scalars()} == {world["sales"], world["company"]}


def test_identity_conflict_by_login_must_be_resolved(app, admin, world):
    worker_id = world["worker"]
    document = _doc({"email": "petr.new@example.test", "login": "et-worker", "name": "Пётр Сотрудник"})
    plan = _import(admin, document).get_json()
    record = plan["records"][0]
    assert not plan["canApply"] and record["unresolved"] and record["identity"]["userId"] == worker_id
    assert plan["summary"]["unresolved"] == 1
    assert _import(admin, document, apply=True).status_code == 409

    same = {"petr.new@example.test": {"identity": "same", "fields": {"email": {"choice": "incoming"}}}}
    plan = _import(admin, document, decisions=same).get_json()
    assert plan["canApply"] and plan["records"][0]["userId"] == worker_id
    assert [c["field"] for c in plan["records"][0]["conflicts"]] == ["email"]
    assert _import(admin, document, decisions=same, apply=True).status_code == 200
    with app.app_context():
        assert db.session.get(User, worker_id).email == "petr.new@example.test"


def test_identity_conflict_as_new_person_needs_another_login(app, admin, world):
    document = _doc({"email": "namesake@example.test", "login": "et-worker", "name": "Тёзка"})
    new = {"namesake@example.test": {"identity": "new"}}
    plan = _import(admin, document, decisions=new).get_json()
    assert not plan["canApply"] and plan["records"][0]["errors"] == ["логин «et-worker» занят — укажите другой"]
    new["namesake@example.test"]["fields"] = {"login": {"choice": "custom", "value": "namesake"}}
    response = _import(admin, document, decisions=new, apply=True)
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        assert db.session.execute(select(User).where(User.email == "namesake@example.test")).scalar_one().login == "namesake"


def test_login_of_user_without_email_is_the_same_person(app, admin, world):
    no_mail = make_user(app, "no.mail", "editor", name="Без Почты")
    document = _doc({"email": "no.mail@example.test", "login": "NO.MAIL", "name": "Без Почты", "position": "Инженер"},
                    {"login": "admin", "name": "Администратор", "role": "viewer", "isActive": False})
    plan = _import(admin, document).get_json()
    records = _records(plan)
    record = records["no.mail@example.test"]
    assert record["userId"] == no_mail and record["identity"] is None and not record["conflicts"]
    assert [c["field"] for c in record["changes"]] == ["email", "position"]
    # Запись без email — по логину; свою роль и активность загрузкой не изменить
    own = records["admin"]
    assert own["userId"] == world["admin"] and own["key"] == "login:admin" and own["action"] == "unchanged"
    assert own["warnings"] == ["Роль: свою учётную запись изменить нельзя — не применяется",
                               "Активен: свою учётную запись изменить нельзя — не применяется"]
    assert _import(admin, document, apply=True).status_code == 200
    with app.app_context():
        assert db.session.get(User, no_mail).email == "no.mail@example.test"
        me = db.session.get(User, world["admin"])
        assert me.role == "admin" and me.is_active


def test_errors_in_file_block_apply(app, admin, world):
    document = _doc(
        {"email": "a@example.test", "name": "А"},
        {"email": "A@example.test", "name": "А дубль"},
        {"email": "broken", "name": "Без почты"},
        {"email": "noname@example.test"},
        {"email": "x@example.test", "name": "Икс", "manager": "nobody@example.test"},
        {"name": "Ни почты, ни логина"},
        {"email": "taken@example.test", "login": "et-head", "name": "Чужой логин", "role": "босс"},
    )
    plan = _import(admin, document, decisions={"taken@example.test": {"identity": "new"}}).get_json()
    records = plan["records"]
    assert records[0]["errors"] == [] and records[1]["errors"] == ["email повторяется (строка 1)"]
    assert records[2]["errors"] == ["некорректный email"]
    assert records[3]["errors"] == ["для нового сотрудника нужно ФИО"]
    # Ненайденный руководитель — предупреждение, не ошибка
    assert records[4]["errors"] == [] and records[4]["warnings"] == [
        "руководитель nobody@example.test не найден ни в системе, ни в файле — не назначается"]
    assert records[5]["errors"] == ["нет email и логина"]
    assert records[6]["errors"] == ["логин «et-head» занят — укажите другой"]
    assert records[6]["warnings"] == ["роль «босс» не распознана — не применяется"]
    assert not plan["canApply"] and plan["summary"]["errors"] == 5
    response = _import(admin, document, apply=True)
    assert response.status_code == 409 and response.get_json()["message"] == "Разберите коллизии и исправьте ошибки"
    with app.app_context():
        assert db.session.query(User).filter_by(email="a@example.test").count() == 0

    def bad(content, fmt, message):
        response = _import(admin, content, fmt=fmt)
        assert response.status_code == 400, response.get_json()
        assert response.get_json()["message"] == message

    bad({"format": "podelu.org-structure"}, "json", "Это не файл сотрудников (format: podelu.employees)")
    bad({"format": "podelu.employees", "version": 2}, "json", "Версия файла 2 не поддерживается — нужна 1")
    bad("{", "json", "Файл не является JSON")
    bad("ФИО;Должность\nА;Б", "csv", "Нужен столбец email (или «Логин»)")
    bad("", "csv", "Файл пустой")
    bad("x", "xml", "Формат: csv или json")
    assert admin.post("/api/users/import", json={"format": "json", "content": "[]",
                                                 "options": {"newUsers": "maybe"}}).status_code == 400


def test_cycle_is_detected(app, admin, world):
    document = _doc(
        {"email": "c1@example.test", "name": "Цикл 1", "manager": "c2@example.test"},
        {"email": "c2@example.test", "name": "Цикл 2", "manager": "c1@example.test"},
    )
    plan = _import(admin, document).get_json()
    assert not plan["canApply"]
    assert all(r["errors"] == ["цикл подчинения: руководитель подчиняется сотруднику"] for r in plan["records"])


def test_admin_role_changes_and_last_admin(app, admin, world):
    other = make_user(app, "admin2", "admin", name="Второй админ", email="admin2@example.test")
    document = _doc({"email": "admin2@example.test", "role": "Редактор", "isActive": "нет"})
    decisions = {"admin2@example.test": {"fields": {"role": {"choice": "incoming"}, "isActive": {"choice": "incoming"}}}}
    # Свой администратор остаётся — второго можно понизить и отключить
    assert _import(admin, document, decisions=decisions, apply=True).status_code == 200
    with app.app_context():
        row = db.session.get(User, other)
        assert row.role == "editor" and not row.is_active


def test_csv_with_russian_headers_own_department_head_and_todo_roles(app, admin, world):
    text = "email;ФИО;Подразделение;Должность;Руководит;Роль;Активен\n" \
           "lead@example.test;Лид Группы;Вектор / Продажи / Группа 1;Руководитель группы;да;Руководитель;нет\n" \
           "staff@example.test;Сотрудник Группы;Вектор / Продажи / Группа 1;;;Сотрудник;да\n" \
           "et-worker@example.test;;;;;Сотрудник;\n"
    plan = _import(admin, text, fmt="csv", options={"newUsers": "file"}).get_json()
    assert plan["canApply"], plan
    assert _records(plan)["et-worker@example.test"]["action"] == "unchanged"   # «Сотрудник» не меняет роль
    response = _import(admin, text, fmt="csv", options={"newUsers": "file"}, apply=True)
    assert response.status_code == 200, response.get_json()
    with app.app_context():
        lead = db.session.execute(select(User).where(User.email == "lead@example.test")).scalar_one()
        staff = db.session.execute(select(User).where(User.email == "staff@example.test")).scalar_one()
        group = db.session.execute(select(Department).where(Department.name == "Группа 1")).scalar_one()
        assert not lead.is_active and lead.role == "manager" and lead.department_id == group.id
        assert staff.is_active and staff.role == "viewer"
        assert db.session.get(DepartmentHead, (group.id, lead.id)).is_primary


@pytest.mark.parametrize("fmt", ["json", "csv", "zip"])
def test_round_trip_export_import_is_unchanged(app, admin, world, fmt):
    admin.patch(f"/api/users/{world['worker']}", json={"managerId": world["admin"]})
    data = admin.get(f"/api/users/export?format={fmt}").data
    avatars = {}
    if fmt == "zip":
        archive = zipfile.ZipFile(io.BytesIO(data))
        content = archive.read("employees.json").decode("utf-8")
        for item in json.loads(content)["employees"]:
            if item.get("avatar"):
                avatars[item["email"].lower()] = hashlib.sha256(archive.read(item["avatar"])).hexdigest()
        assert list(avatars) == ["et-worker@example.test"]
    else:
        content = data.decode("utf-8")
    plan = _import(admin, content, fmt="csv" if fmt == "csv" else "json", avatars=avatars).get_json()
    assert plan["canApply"] and plan["summary"]["create"] == 0 and plan["summary"]["conflicts"] == 0
    assert plan["summary"]["update"] == 0, [r for r in plan["records"] if r["action"] != "unchanged"]
    assert plan["notes"] == []


def _snapshot_people(app):
    with app.app_context():
        snap = OrgSnapshot()
        people = {}
        for user in snap.users.values():
            manager = snap.users.get(user.manager_id) if user.manager_id else None
            heads = sorted((" / ".join(snap.path(d)), p) for d, heads in snap.heads.items() for uid, p in heads if uid == user.id)
            avatar = db.session.get(UserAvatar, user.id)
            people[user.login] = (user.name, (user.email or "").lower(), user.position, user.role, user.is_active,
                                  " / ".join(snap.path(user.department_id)), manager.login if manager else None,
                                  user.reports_to_none, tuple(heads), avatar.sha256 if avatar else None)
        return people


def test_round_trip_into_empty_system(app, admin, world):
    """Структура и сотрудники выгружаются, система очищается, загрузка возвращает то же самое."""
    ceo = make_user(app, "ceo", "viewer", name="Первое Лицо", email="ceo@example.test", reports_to_none=True)
    admin.patch(f"/api/users/{ceo}", json={"departmentId": world["company"]})
    admin.put(f"/api/departments/{world['company']}/heads", json={"userIds": [ceo]})
    admin.patch(f"/api/users/{world['worker']}", json={"managerId": ceo})
    admin.patch(f"/api/users/{world['head']}", json={"isActive": False})
    before = _snapshot_people(app)
    structure = json.loads(admin.get("/api/org/export").data)
    archive = zipfile.ZipFile(io.BytesIO(admin.get("/api/users/export?format=zip").data))
    content = archive.read("employees.json").decode("utf-8")
    with app.app_context():
        db.session.query(DepartmentHead).delete()
        db.session.execute(text("UPDATE users SET department_id = NULL, manager_id = NULL"))
        db.session.execute(text("UPDATE departments SET parent_id = NULL"))
        db.session.query(Department).delete()
        db.session.query(UserAvatar).delete()
        db.session.query(User).filter(User.login != "admin").delete()
        db.session.commit()
    assert admin.post("/api/org/import/structure", json={"bundle": structure, "apply": True}).status_code == 200
    employees = json.loads(content)["employees"]
    avatars = {item["email"].lower(): hashlib.sha256(archive.read(item["avatar"])).hexdigest()
               for item in employees if item.get("avatar")}
    applied = _import(admin, content, options={"newUsers": "file"}, avatars=avatars, apply=True)
    assert applied.status_code == 200, applied.get_json()
    body = applied.get_json()
    assert body["summary"]["create"] == 3 and body["avatarUploads"] == ["et-worker@example.test"]
    photo = next(item["avatar"] for item in employees if item.get("avatar"))
    uploaded = admin.post("/api/users/import/avatars", content_type="multipart/form-data",
                          data={str(body["ids"]["et-worker@example.test"]): (io.BytesIO(archive.read(photo)), "a.png")})
    assert uploaded.get_json() == {"saved": 1, "errors": {}}
    assert _snapshot_people(app) == before
    with app.app_context():
        assert all(u.password_hash is None for u in db.session.execute(select(User).where(User.login != "admin")).scalars())


def test_avatars_after_import_and_avatar_conflict(app, admin, world):
    document = _doc({"email": "photo@example.test", "name": "С фото"}, {"email": "et-worker@example.test"})
    other_photo = hashlib.sha256(b"another").hexdigest()
    avatars = {"photo@example.test": other_photo, "et-worker@example.test": other_photo}
    plan = _import(admin, document, avatars=avatars).get_json()
    records = _records(plan)
    assert records["photo@example.test"]["avatar"] is True and plan["summary"]["avatars"] == 1
    conflict = records["et-worker@example.test"]["conflicts"][0]
    assert conflict["field"] == "avatar" and conflict["choices"] == ["current", "incoming"]
    # То же фото, что в системе, — не коллизия
    same = {"et-worker@example.test": hashlib.sha256(PNG).hexdigest()}
    assert _import(admin, _doc({"email": "et-worker@example.test"}), avatars=same).get_json()["records"][0]["action"] == "unchanged"
    applied = _import(admin, document, avatars=avatars, apply=True).get_json()
    assert applied["avatarUploads"] == ["photo@example.test"]
    new_id = applied["ids"]["photo@example.test"]
    response = admin.post("/api/users/import/avatars", content_type="multipart/form-data", data={
        str(new_id): (io.BytesIO(JPEG), "1.jpg"), str(world["head"]): (io.BytesIO(b"<svg/>"), "x.svg"),
        "999999": (io.BytesIO(PNG), "y.png"), str(world["worker"]): (io.BytesIO(PNG + b"\x00" * (2 * 1024 * 1024)), "z.png"),
    })
    body = response.get_json()
    assert body["saved"] == 1
    assert body["errors"] == {str(world["head"]): "допустимы PNG, JPEG, GIF и WebP", "999999": "сотрудник не найден",
                              str(world["worker"]): "файл больше 2 МБ"}
    assert admin.get(f"/api/users/{new_id}/avatar").data == JPEG
    assert admin.post("/api/users/import/avatars", data={}).status_code == 400


def test_todo_planner_files(app, admin):
    """Настоящая выгрузка сотрудников «По Делу» 1.4: JSON, CSV и ZIP (tests/fixtures/todo_planner)."""
    bundle = json.loads((FIXTURES / "todo_structure.json").read_text(encoding="utf-8"))
    assert admin.post("/api/org/import/structure", json={"bundle": bundle, "apply": True}).status_code == 200
    document = (FIXTURES / "todo_employees.json").read_text(encoding="utf-8")
    plan = _import(admin, document).get_json()
    records = _records(plan)
    assert plan["notes"] == ["Поля «По Делу», которых нет в «Пульсе», не загружаются: функциональные руководители, "
                             "полномочия, часовой пояс, видимость"]
    # Администратор «По Делу» с логином admin — это администратор «Пульса» без email
    own = records["admin@todo-planner.local"]
    assert own["userId"] is not None and own["identity"] is None
    assert [c["field"] for c in own["changes"]] == ["email"] and [c["field"] for c in own["conflicts"]] == ["name"]
    assert plan["canApply"], [r for r in plan["records"] if r["errors"] or r["unresolved"]]
    assert plan["summary"]["create"] == 7
    applied = _import(admin, document, options={"newUsers": "file"}, apply=True)
    assert applied.status_code == 200, applied.get_json()
    with app.app_context():
        users = {u.login: u for u in db.session.execute(select(User)).scalars()}
        assert users["a.kovaleva"].role == "manager" and users["a.kovaleva"].email == "sales.head@vector.test"
        assert users["i.sokolova"].role == "admin" and users["i.sokolova"].reports_to_none
        assert users["d.orlov"].role == "viewer" and users["p.ivanov"].role == "viewer"
        snap = OrgSnapshot()
        # Руководитель из файла совпадает с руководителем по структуре (руководит Лабораторией) — явным не записан
        assert not users["e.smirnova"].is_active and users["e.smirnova"].manager_id is None
        assert snap.structure_manager(users["e.smirnova"]) == users["i.sokolova"].id
        assert snap.direct_manager(users["p.ivanov"])[0] == users["d.orlov"].id
        assert snap.direct_manager(users["d.orlov"])[0] == users["a.kovaleva"].id
        lab = db.session.execute(select(Department).where(Department.name == "Лаборатория")).scalar_one()
        assert db.session.get(DepartmentHead, (lab.id, users["i.sokolova"].id)) is not None
        assert users["admin"].email == "admin@todo-planner.local" and users["admin"].name == "Администратор"
    # Повторная загрузка CSV и ZIP «По Делу» ничего не меняет (кроме имени администратора — коллизия)
    csv_text = (FIXTURES / "todo_employees.csv").read_bytes().decode("utf-8")
    plan = _import(admin, csv_text, fmt="csv").get_json()
    changed = [r for r in plan["records"] if r["action"] != "unchanged"]
    assert changed == [] and plan["summary"]["conflicts"] == 1
    archive = zipfile.ZipFile(FIXTURES / "todo_employees.zip")
    inside = json.loads(archive.read("employees.json"))
    avatars = {item["email"].lower(): hashlib.sha256(archive.read(item["avatar"])).hexdigest()
               for item in inside["employees"] if item.get("avatar")}
    plan = _import(admin, archive.read("employees.json").decode("utf-8"), avatars=avatars).get_json()
    assert plan["summary"]["avatars"] == 2 and plan["summary"]["update"] == 2


def test_only_admin_transfers_employees(app, admin, world):
    for role in ("manager", "editor", "viewer"):
        client = new_client(app, f"et-{role}", role)
        assert client.get("/api/users/export").status_code == 403
        assert _import(client, _doc()).status_code == 403
        assert client.post("/api/users/import/avatars").status_code == 403
