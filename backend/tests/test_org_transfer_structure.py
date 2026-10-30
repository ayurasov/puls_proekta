"""Файл структуры podelu.org-structure (ADR-0008): выгрузка, план загрузки и применение; совместимость с «По Делу»."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.extensions import db
from app.models import AppSetting, AuditLog, Department, DepartmentHead, User
from tests.conftest import make_user, new_client

FIXTURES = Path(__file__).parent / "fixtures" / "todo_planner"


def _dep(admin, name, parent=None, kind="department", sort_order=None):
    body = {"name": name, "parentId": parent, "kind": kind}
    if sort_order is not None:
        body["sortOrder"] = sort_order
    res = admin.post("/api/departments", json=body)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


@pytest.fixture
def world(app, admin):
    head = make_user(app, "st-head", "manager", name="Анна Руководитель", email="St-Head@Example.test")
    lead = make_user(app, "st-lead", "editor", name="Без почты")
    company = _dep(admin, "ООО «Вектор»", kind="company")
    sales = _dep(admin, "Продажи", company, sort_order=2)
    _dep(admin, "Разработка", company, sort_order=1)
    group = _dep(admin, "Группа B2B", sales, kind="group")
    assert admin.put(f"/api/departments/{sales}/heads", json={"userIds": [head]}).status_code == 200
    assert admin.put(f"/api/departments/{group}/heads", json={"userIds": [lead]}).status_code == 200
    return {"head": head, "lead": lead, "company": company, "sales": sales, "group": group}


def _export(client):
    response = client.get("/api/org/export")
    assert response.status_code == 200, response.get_json()
    assert response.headers["Content-Disposition"].startswith('attachment; filename="puls-structure-')
    assert response.headers["Content-Disposition"].endswith('.json"')
    return json.loads(response.data)


def _plan(client, bundle, **extra):
    return client.post("/api/org/import/structure", json={"bundle": bundle, **extra})


def _wipe_structure(app):
    with app.app_context():
        db.session.query(DepartmentHead).delete()
        db.session.query(User).update({User.department_id: None})
        db.session.execute(text("UPDATE departments SET parent_id = NULL"))
        db.session.query(Department).delete()
        db.session.query(AppSetting).filter(AppSetting.key == "org").delete()
        db.session.commit()


def _shape(bundle):
    return [(d["name"], d["kind"], d["sortOrder"], [(h["email"], h["primary"]) for h in d["heads"]])
            for d in bundle["departments"]]


def test_export_contains_tree_settings_and_heads(app, admin, world):
    bundle = _export(admin)
    assert bundle["format"] == "podelu.org-structure" and bundle["version"] == 1 and bundle["product"] == "puls"
    assert bundle["exportedAt"].endswith("Z") and bundle["app"]
    names = [item["name"] for item in bundle["departments"]]
    # Родители раньше детей, внутри уровня — по порядку
    assert names == ["ООО «Вектор»", "Разработка", "Продажи", "Группа B2B"]
    sales = next(item for item in bundle["departments"] if item["name"] == "Продажи")
    assert sales["parent"] == bundle["departments"][0]["key"] and sales["sortOrder"] == 2
    # email как в системе; логин — расширение «Пульса» для сотрудников без email
    assert sales["heads"] == [{"email": "St-Head@Example.test", "primary": True, "login": "st-head"}]
    group = next(item for item in bundle["departments"] if item["name"] == "Группа B2B")
    assert group["heads"] == [{"email": "", "primary": True, "login": "st-lead"}] and group["kind"] == "group"
    assert set(bundle["settings"]) == {"org_unit_kinds", "org_max_depth"}
    assert any(kind["value"] == "group" for kind in bundle["settings"]["org_unit_kinds"])
    assert bundle["settings"]["org_max_depth"] == 15
    # Не-ASCII не экранируется
    assert "ООО «Вектор»".encode() in admin.get("/api/org/export").data


def test_round_trip_into_empty_system(app, admin, world):
    admin.put("/api/org/settings", json={"unitKinds": _export(admin)["settings"]["org_unit_kinds"], "maxDepth": 9})
    bundle = _export(admin)
    _wipe_structure(app)
    preview = _plan(admin, bundle)
    assert preview.status_code == 200, preview.get_json()
    body = preview.get_json()
    assert body["canApply"] and body["summary"]["create"] == 4 and body["summary"]["addHeads"] == 2
    assert [item["key"] for item in body["settings"]] == ["org_max_depth"]
    assert body["settingsApplied"] is True and body["notes"] == []
    with app.app_context():
        assert db.session.query(Department).count() == 0   # просмотр ничего не пишет
        assert db.session.get(AppSetting, "org") is None
    applied = _plan(admin, bundle, apply=True)
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()["applied"] is True
    again = _export(admin)
    assert _shape(again) == _shape(bundle)
    assert again["settings"] == bundle["settings"]
    with app.app_context():
        assert {d.org_source for d in db.session.execute(select(Department)).scalars()} == {"transfer"}
        assert db.session.execute(select(AuditLog).where(AuditLog.section == "Структура",
                                                         AuditLog.action == "import")).first()
    # Повторная загрузка того же файла ничего не меняет
    repeat = _plan(admin, bundle).get_json()
    assert repeat["summary"]["create"] == 0 and repeat["summary"]["update"] == 0 and not repeat["settings"]
    assert repeat["summary"]["addHeads"] == 0 and {h["action"] for h in repeat["heads"]} == {"exists"}


def test_matching_by_path_updates_kind_and_keeps_missing(app, admin, world):
    bundle = _export(admin)
    bundle["departments"] = [d for d in bundle["departments"] if d["name"] != "Разработка"]
    for item in bundle["departments"]:
        item["key"] = "x-" + item["key"]
        item["parent"] = "x-" + item["parent"] if item["parent"] else None
        if item["name"] == "Группа B2B":
            item["kind"] = "sector"
            item["name"] = "  группа   b2b "
            item["externalId"] = "B2B-1"
    plan = _plan(admin, bundle, applySettings=False).get_json()
    group = next(d for d in plan["departments"] if d["path"].endswith("b2b"))
    assert group["action"] == "update" and group["changes"] == [{"field": "kind", "old": "group", "new": "sector"}]
    assert plan["missingInFile"] == ["ООО «Вектор» / Разработка"] and plan["summary"]["missingInFile"] == 1
    assert plan["summary"]["create"] == 0 and plan["settings"] == [] and plan["settingsApplied"] is False
    assert _plan(admin, bundle, applySettings=False, apply=True).status_code == 200
    with app.app_context():
        row = db.session.get(Department, world["group"])
        assert row.kind == "sector" and row.name == "Группа B2B" and row.external_id == "B2B-1"
        assert db.session.query(Department).count() == 4   # «Разработка» осталась


def test_errors_unknown_kind_depth_and_bad_files(app, admin, world):
    bundle = _export(admin)
    bundle["departments"].append({"key": "new", "name": "Лаборатория", "parent": bundle["departments"][0]["key"],
                                  "kind": "lab"})
    plan = _plan(admin, bundle).get_json()
    assert not plan["canApply"] and plan["errors"] == ["ООО «Вектор» / Лаборатория: тип «lab» не задан в настройках"]
    response = _plan(admin, bundle, apply=True)
    assert response.status_code == 409 and response.get_json()["error"] == "import_has_errors"
    with app.app_context():
        assert db.session.query(Department).filter_by(name="Лаборатория").count() == 0
    # Тип из настроек того же файла — не ошибка
    bundle["settings"]["org_unit_kinds"].append({"value": "lab", "label": "Лаборатория", "color": "#123456"})
    assert _plan(admin, bundle).get_json()["canApply"]
    # …но без переноса настроек — ошибка
    assert not _plan(admin, bundle, applySettings=False).get_json()["canApply"]
    bundle["settings"]["org_max_depth"] = 2
    plan = _plan(admin, bundle).get_json()
    assert "Вложенность после загрузки — 3 уровней, а допустимо 2: увеличьте «Глубину вложенности»" in plan["errors"]
    bundle["settings"]["org_max_depth"] = "много"
    assert "org_max_depth: ожидается целое число" in _plan(admin, bundle).get_json()["errors"]
    bundle["settings"]["org_max_depth"] = 15
    bundle["settings"]["org_unit_kinds"] = [{"value": "Bad Kind", "label": "x"}]
    assert _plan(admin, bundle).get_json()["errors"][0].startswith("org_unit_kinds: код типа")
    del bundle["settings"]["org_unit_kinds"]
    bundle["departments"].append({"key": "dup", "name": "лаборатория ", "parent": bundle["departments"][0]["key"],
                                  "kind": "department"})
    assert any("уже есть подразделение с таким названием" in e for e in _plan(admin, bundle).get_json()["errors"])

    def bad(document, message):
        response = _plan(admin, document)
        assert response.status_code == 400, response.get_json()
        assert response.get_json()["message"] == message
        assert response.get_json()["details"][0]["field"] == "bundle"

    good = _export(admin)
    bad({"format": "other"}, "Это не файл структуры компании (format: podelu.org-structure)")
    bad({**good, "version": 9}, "Версия файла 9 не поддерживается — нужна 1")
    bad("{oops", "Файл не является JSON")
    bad({**good, "departments": None}, "В файле нет списка подразделений")
    bad({**good, "departments": [{"key": str(i), "name": "x"} for i in range(5001)]},
        "Не больше 5000 подразделений в одном файле")
    bad({**good, "departments": [{"key": "a", "name": ""}]}, "Подразделение №1: нужны key и name")
    bad({**good, "departments": [{"key": "a", "name": "A"}, {"key": "a", "name": "B"}]},
        "Подразделение №2: key «a» повторяется")
    bad({**good, "departments": [{"key": "b", "name": "B", "parent": "a"}, {"key": "a", "name": "A"}]},
        "Подразделение «B»: родитель «a» должен идти в файле раньше")
    bad({**good, "departments": ["x"]}, "Подразделение №1: ожидается объект")
    # Строкой JSON — тоже можно
    assert _plan(admin, json.dumps(good, ensure_ascii=False)).get_json()["canApply"]


def test_heads_of_missing_employees_wait_for_employee_import(app, admin, world):
    bundle = _export(admin)
    sales = next(item for item in bundle["departments"] if item["name"] == "Продажи")
    sales["heads"].append({"email": "New-Head@example.test", "primary": False})
    group = next(item for item in bundle["departments"] if item["name"] == "Группа B2B")
    group["heads"] = [{"email": "st-head@example.test", "primary": True}]
    plan = _plan(admin, bundle).get_json()
    pending = [h for h in plan["heads"] if h["action"] == "pending"]
    assert [h["email"] for h in pending] == ["new-head@example.test"]
    added = [h for h in plan["heads"] if h["action"] == "add"]
    assert [(h["path"], h["name"]) for h in added] == [("ООО «Вектор» / Продажи / Группа B2B", "Анна Руководитель")]
    assert plan["canApply"] and plan["summary"]["pendingHeads"] == 1 and plan["summary"]["addHeads"] == 1
    assert _plan(admin, bundle, apply=True).status_code == 200
    with app.app_context():
        heads = {(h.user_id, h.is_primary) for h in db.session.execute(
            select(DepartmentHead).where(DepartmentHead.department_id == world["group"])).scalars()}
        # Руководители только добавляются; основной уже был — новый не основной
        assert heads == {(world["lead"], True), (world["head"], False)}


def test_todo_planner_bundle(app, admin):
    """Настоящая выгрузка «По Делу» 1.4 (tests/fixtures/todo_planner, сделана его export_structure)."""
    bundle = json.loads((FIXTURES / "todo_structure.json").read_text(encoding="utf-8"))
    ceo = make_user(app, "ceo", "admin", name="Ирина Соколова", email="ceo@vector.test")
    make_user(app, "sales", "manager", name="Анна Ковалёва", email="sales.head@VECTOR.test")
    plan = _plan(admin, bundle)
    assert plan.status_code == 200, plan.get_json()
    body = plan.get_json()
    assert body["canApply"], body["errors"]
    assert body["summary"]["create"] == 5 and body["summary"]["addHeads"] == 3 and body["summary"]["pendingHeads"] == 2
    assert [item["key"] for item in body["settings"]] == ["org_max_depth"]
    assert body["settings"][0]["incoming"] == "7"
    assert body["notes"] == ["Настройки «По Делу», которых нет в «Пульсе», не переносятся: термины структуры, длина "
                             "цепочки поручений, руководителем может быть любой сотрудник, вид оргсхемы по умолчанию, кому "
                             "руководитель может поручать, уведомления о поручении «через голову», что видит руководитель"]
    assert _plan(admin, bundle, apply=True).status_code == 200
    exported = _export(admin)
    assert [(d["name"], d["kind"], d["sortOrder"]) for d in exported["departments"]] == \
        [(d["name"], d["kind"], d["sortOrder"]) for d in bundle["departments"]]
    assert exported["departments"][0]["externalId"] == "ORG-1" and exported["settings"]["org_max_depth"] == 7
    lab = next(d for d in exported["departments"] if d["name"] == "Лаборатория")
    assert lab["heads"] == [{"email": "ceo@vector.test", "primary": False, "login": "ceo"}]
    with app.app_context():
        assert db.session.execute(select(DepartmentHead).where(DepartmentHead.user_id == ceo,
                                                               DepartmentHead.is_primary.is_(True))).first()


def test_only_admin_transfers_structure(app, admin, world):
    for role in ("manager", "editor", "viewer"):
        client = new_client(app, f"st-{role}", role)
        assert client.get("/api/org/export").status_code == 403
        assert client.post("/api/org/import/structure", json={"bundle": {}}).status_code == 403
    assert app.test_client().get("/api/org/export").status_code == 401
