"""Быстрый импорт структуры из CSV (как в «По Делу», ADR-0044 этап 4): план, применение, ошибки, циклы."""

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Department, DepartmentHead, User
from app.services.org import OrgSnapshot
from app.services.org_import import parse_csv, plan_import, unique_login
from tests.conftest import make_user, new_client


@pytest.fixture
def org(app, admin):
    """Компания (ceo) → Продажи (head) → seller; Закупки (buyer_head) → buyer."""
    ids = {name: make_user(app, f"s4-{name}", "editor", name=f"S4 {name}", email=f"{name}@example.com")
           for name in ("ceo", "head", "seller", "buyer_head", "buyer")}
    deps = {}
    for key, name, parent, kind in (("c", "Компания", None, "company"), ("sales", "Продажи", "c", "department"),
                                    ("buy", "Закупки", "c", "department")):
        res = admin.post("/api/departments", json={"name": name, "parentId": deps.get(parent), "kind": kind})
        assert res.status_code == 201, res.get_json()
        deps[key] = res.get_json()["id"]
    for name, dep in (("ceo", "c"), ("head", "sales"), ("buyer_head", "buy")):
        assert admin.put(f"/api/departments/{deps[dep]}/heads", json={"userIds": [ids[name]]}).status_code == 200
    for name, dep in (("ceo", "c"), ("head", "sales"), ("seller", "sales"), ("buyer_head", "buy"), ("buyer", "buy")):
        assert admin.patch(f"/api/users/{ids[name]}", json={"departmentId": deps[dep]}).status_code == 200
    return {"ids": ids, "deps": deps}


# Пример из tests/test_org_stage4.py «По Делу»
CSV = """email;ФИО;Подразделение;Должность;Руководитель;Руководит
head@example.com;;Компания / Продажи;Начальник отдела продаж;;да
seller@example.com;;Компания / Продажи / Ключевые клиенты;Менеджер;keylead@example.com;
keylead@example.com;Ключевой Руководитель;Компания / Продажи / Ключевые клиенты;Руководитель группы;head@example.com;да
buyer@example.com;;Компания / Закупки;;head@example.com;
"""


def _csv(client, text, **extra):
    return client.post("/api/org/import/csv", json={"csv": text, **extra})


def test_csv_preview_shows_changes_without_writing(app, admin, org):
    preview = _csv(admin, CSV, createMissing=True)
    assert preview.status_code == 200, preview.get_json()
    body = preview.get_json()
    assert body["source"] == "csv"
    assert body["summary"] == {"rows": 4, "createDepartments": 1, "createUsers": 1, "updateUsers": 3, "unchanged": 0,
                               "deactivateUsers": 0, "errors": 0}
    assert body["departments"] == [{"key": "new:1", "path": "Компания / Продажи / Ключевые клиенты",
                                    "name": "Ключевые клиенты", "kind": "department"}]
    assert body["canApply"] is True and body["cycles"] == [] and body["deactivate"] == []
    users = {user["email"]: user for user in body["users"]}
    assert {change["field"] for change in users["seller@example.com"]["changes"]} == {"department", "position"}
    # Руководитель группы — руководитель по структуре, явный не нужен
    assert users["seller@example.com"]["managerExplicit"] is False
    assert users["seller@example.com"]["manager"] == "Ключевой Руководитель"
    assert users["buyer@example.com"]["managerExplicit"] is True
    assert users["keylead@example.com"]["action"] == "create" and users["keylead@example.com"]["userId"] is None
    assert users["head@example.com"]["changes"] == [{"field": "position", "old": "", "new": "Начальник отдела продаж"}]
    with app.app_context():
        assert db.session.query(Department).filter_by(name="Ключевые клиенты").count() == 0
        assert db.session.query(User).filter_by(email="keylead@example.com").count() == 0


def test_csv_apply_builds_the_structure(app, admin, org):
    ids = org["ids"]
    applied = _csv(admin, CSV, createMissing=True, apply=True)
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()["applied"] is True
    with app.app_context():
        group = db.session.execute(select(Department).where(Department.name == "Ключевые клиенты")).scalar_one()
        assert group.parent_id == org["deps"]["sales"] and group.org_source == "csv" and group.kind == "department"
        lead = db.session.execute(select(User).where(User.email == "keylead@example.com")).scalar_one()
        assert lead.password_hash is None and lead.department_id == group.id and lead.login == "keylead"
        assert lead.role == "viewer" and lead.auth_source == "local" and lead.is_active and lead.org_source == "csv"
        assert db.session.get(DepartmentHead, (group.id, lead.id)).is_primary
        snap = OrgSnapshot()
        assert snap.direct_manager(snap.users[ids["seller"]]) == (lead.id, "structure")
        assert snap.direct_manager(snap.users[lead.id])[0] == ids["head"]
        assert snap.direct_manager(snap.users[ids["buyer"]]) == (ids["head"], "explicit")
        assert db.session.get(User, ids["head"]).position == "Начальник отдела продаж"
        audit = {(row.section, row.field) for row in db.session.execute(select(AuditLog)).scalars()}
        assert ("Пользователи", "department") in audit and ("Структура", "") in audit
    # Повторный импорт того же файла ничего не меняет
    again = _csv(admin, CSV, createMissing=True).get_json()
    assert again["summary"]["updateUsers"] == 0 and again["summary"]["createDepartments"] == 0
    assert again["summary"]["unchanged"] == 4


def test_csv_errors_block_the_whole_import(app, admin, org):
    broken = CSV + "ghost@example.com;;Компания;;nobody@example.com;\nhead@example.com;;Компания;;;\n" \
                   ";;Компания;;;\nnew@example.com;;;;;\nself@example.com;Сам;Компания;;self@example.com;\n"
    response = _csv(admin, broken, apply=True)
    assert response.status_code == 409
    body = response.get_json()
    assert body["error"] == "import_has_errors" and body["message"] == "Исправьте ошибки в файле"
    errors = {(user["email"], user["line"]): user["errors"] for user in body["users"] if user["errors"]}
    assert errors[("keylead@example.com", 4)] == ["пользователь не найден (включите «создавать новых»)"]
    assert errors[("ghost@example.com", 6)] == ["пользователь не найден (включите «создавать новых»)",
                                                "руководитель nobody@example.com не найден"]
    assert errors[("head@example.com", 7)][:2] == ["email повторяется (строка 2)", "сотрудник уже указан в строке 2"]
    assert errors[("", 8)][0] == "нет email и логина"
    assert "не указано подразделение" in errors[("new@example.com", 9)]
    assert body["summary"]["errors"] == len(errors) and body["canApply"] is False
    with app.app_context():
        assert db.session.query(Department).filter_by(name="Ключевые клиенты").count() == 0
        assert db.session.get(User, org["ids"]["head"]).position == ""
    create = _csv(admin, broken, createMissing=True).get_json()
    by_line = {user["line"]: user["errors"] for user in create["users"]}
    assert "для нового сотрудника нужно ФИО" in by_line[6]
    assert "сотрудник указан своим руководителем" in by_line[10]


def test_csv_format_errors(app, admin, org):
    def bad(text, message):
        response = _csv(admin, text)
        assert response.status_code == 400, response.get_json()
        assert response.get_json()["message"] == message

    bad("a;b\n1;2", "Нужен столбец email или login (логин)")
    bad("email;ФИО\nx@example.com;X", "Нужен столбец department (подразделение, путь через «/»)")
    bad("   \n", "Файл пустой")
    bad("email;department\n" + "x@example.com;A\n" * 5001, "Не больше 5000 строк за один импорт")
    assert admin.post("/api/org/import/csv", json={"csv": 5}).status_code == 400


def test_csv_parsing_aliases_delimiters_and_paths():
    rows = parse_csv("\ufeffE-mail,Login,Department_Path,Title,Manager_Email,Is_Head,External_ID,Name\n"
                     "A@X.RU,anna,Компания > Продажи \\ B2B,  Менеджер ,BOSS@X.RU,yes,T-1,Анна\xa0 Иванова\n\n")
    assert len(rows) == 1
    row = rows[0]
    assert (row.line, row.email, row.login, row.department) == (2, "a@x.ru", "anna", ["Компания", "Продажи", "B2B"])
    assert (row.position, row.manager_email, row.is_head, row.external_id, row.name) == \
        ("Менеджер", "boss@x.ru", True, "T-1", "Анна Иванова")
    russian = parse_csv("Почта;Логин;Отдел;Должность;Почта руководителя;Руководитель подразделения;Табельный номер\n"
                        "b@x.ru;;Компания/Склад;;;нет;\n")
    assert russian[0].department == ["Компания", "Склад"] and not russian[0].is_head and russian[0].position is None


def test_login_matching_creation_and_cycles(app, admin, org):
    ids = org["ids"]
    text = "login;ФИО;department\ns4-seller;;Компания / Закупки\nIvanov.II@corp;Иванов;Компания\n"
    plan = _csv(admin, text, createMissing=True).get_json()
    assert plan["users"][0]["userId"] == ids["seller"] and plan["users"][0]["changes"][0]["field"] == "department"
    assert _csv(admin, text, createMissing=True, apply=True).status_code == 200
    with app.app_context():
        assert db.session.execute(select(User).where(User.name == "Иванов")).scalar_one().login == "ivanov.iicorp"
    taken = {"user", "anna", "anna2"}
    assert unique_login("Anna", taken) == "anna3" and unique_login("Пётр", taken) == "user2"
    cycle = "email;department;manager_email\nhead@example.com;Компания / Продажи;seller@example.com\n" \
            "seller@example.com;Компания / Продажи;head@example.com\n"
    with app.app_context():
        result = plan_import(parse_csv(cycle)).as_dict()
    assert result["canApply"] is False and result["cycles"] == ["S4 head", "S4 seller"]
    assert all("цикл подчинения: руководитель подчиняется сотруднику" in user["errors"] for user in result["users"])


def test_new_root_is_a_company_and_reports_to_none_is_cleared(app, admin, org):
    ids = org["ids"]
    admin.patch(f"/api/users/{ids['seller']}", json={"reportsToNone": True})
    text = "email;department;manager\nseller@example.com;Компания / Продажи;ceo@example.com\n"
    plan = _csv(admin, text).get_json()
    fields = [change["field"] for change in plan["users"][0]["changes"]]
    assert fields == ["manager", "reportsToNone"]
    assert _csv(admin, text, apply=True).status_code == 200
    with app.app_context():
        seller = db.session.get(User, ids["seller"])
        assert seller.manager_id == ids["ceo"] and seller.reports_to_none is False
    # Новый корень рядом с существующим — обычный отдел; в пустой структуре — компания
    other = _csv(admin, "email;department\nbuyer@example.com;Холдинг\n").get_json()
    assert other["departments"][0]["kind"] == "department"


def test_only_admin_imports_csv(app, admin, org):
    for role in ("manager", "editor", "viewer"):
        assert new_client(app, f"csv-{role}", role).post("/api/org/import/csv", json={"csv": CSV}).status_code == 403


def test_apply_is_one_transaction(app, admin, org, monkeypatch):
    """Сбой посреди применения (подразделения уже добавлены в сессию) — в базе не остаётся ничего."""
    from app.services import org_import

    def broken(*_args, **_kwargs):
        raise RuntimeError("сбой при создании сотрудника")

    monkeypatch.setattr(org_import, "unique_login", broken)
    with pytest.raises(RuntimeError):
        _csv(admin, CSV, createMissing=True, apply=True)
    with app.app_context():
        assert db.session.query(Department).filter_by(name="Ключевые клиенты").count() == 0
        assert db.session.get(User, org["ids"]["head"]).position == ""
        assert db.session.query(AuditLog).filter(AuditLog.entity == "Ключевые клиенты").count() == 0
