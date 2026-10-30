"""Структура компании (ADR-0008): подразделения, руководители, место сотрудника, вычисление руководителя."""

from tests.conftest import make_user, new_client
from app.extensions import db


def _dep(admin, name, parent=None, kind="department", status=201):
    res = admin.post("/api/departments", json={"name": name, "parentId": parent, "kind": kind})
    assert res.status_code == status, res.get_json()
    return res.get_json()


def _people(client):
    return {p["name"]: p for p in client.get("/api/org/tree").get_json()["people"]}


def test_tree_heads_and_direct_manager(app, admin):
    company = _dep(admin, "ООО «Компания»", kind="company")
    sales = _dep(admin, "Продажи", company["id"])
    ids = {name: make_user(app, login, "editor", name=name) for login, name in (
        ("ceo", "Иванов Иван"), ("head", "Петрова Анна"), ("rep", "Сидоров Пётр"), ("lone", "Без отдела"))}
    for name, dep in (("Иванов Иван", company), ("Петрова Анна", sales), ("Сидоров Пётр", sales)):
        assert admin.patch(f"/api/users/{ids[name]}", json={"departmentId": dep["id"]}).status_code == 200
    assert admin.put(f"/api/departments/{company['id']}/heads", json={"userIds": [ids["Иванов Иван"]]}).status_code == 200
    assert admin.put(f"/api/departments/{sales['id']}/heads", json={"userIds": [ids["Петрова Анна"]]}).status_code == 200

    data = admin.get("/api/org/tree").get_json()
    deps = {d["name"]: d for d in data["departments"]}
    assert [d["name"] for d in data["departments"]] == ["ООО «Компания»", "Продажи"]
    assert deps["Продажи"]["primaryHeadId"] == ids["Петрова Анна"] and deps["Продажи"]["level"] == 2
    assert deps["ООО «Компания»"]["branchEmployeeCount"] == 3 and deps["Продажи"]["employeeCount"] == 2
    people = _people(admin)
    # Сотрудник отдела → руководитель отдела; руководитель отдела → руководитель компании; первое лицо — никто
    assert people["Сидоров Пётр"]["directManagerId"] == ids["Петрова Анна"]
    assert people["Петрова Анна"]["directManagerId"] == ids["Иванов Иван"]
    assert people["Иванов Иван"]["managerSource"] == "none"
    assert people["Без отдела"]["directManagerId"] is None

    card = admin.get(f"/api/org/users/{ids['Сидоров Пётр']}").get_json()
    assert card["departmentPath"] == ["ООО «Компания»", "Продажи"]
    assert card["chain"] == [ids["Петрова Анна"], ids["Иванов Иван"]]
    assert admin.get(f"/api/org/users/{ids['Петрова Анна']}").get_json()["directReports"] == [ids["Сидоров Пётр"]]

    # Явный руководитель и «первое лицо»
    admin.patch(f"/api/users/{ids['Сидоров Пётр']}", json={"managerId": ids["Иванов Иван"]})
    assert _people(admin)["Сидоров Пётр"]["managerSource"] == "explicit"
    admin.patch(f"/api/users/{ids['Сидоров Пётр']}", json={"reportsToNone": True})
    person = _people(admin)["Сидоров Пётр"]
    assert person["directManagerId"] is None and person["managerId"] is None
    # Цикл подчинения запрещён
    res = admin.patch(f"/api/users/{ids['Иванов Иван']}", json={"managerId": ids["Петрова Анна"]})
    assert res.status_code == 409


def test_department_rules(app, admin):
    root = _dep(admin, "Компания", kind="company")
    child = _dep(admin, "Отдел", root["id"])
    _dep(admin, "отдел ", root["id"], status=409)  # повтор названия среди соседей
    _dep(admin, "Отдел")  # на другом уровне — можно
    _dep(admin, "X", root["id"], kind="unknown", status=400)
    assert admin.patch(f"/api/departments/{root['id']}", json={"parentId": child["id"]}).status_code == 409
    assert admin.delete(f"/api/departments/{root['id']}").status_code == 409
    user_id = make_user(app, "member", name="Сотрудник")
    admin.patch(f"/api/users/{user_id}", json={"departmentId": child["id"]})
    assert admin.delete(f"/api/departments/{child['id']}").status_code == 200
    assert _people(admin)["Сотрудник"]["departmentId"] is None


def test_max_depth_and_settings(app, admin):
    res = admin.put("/api/org/settings", json={"unitKinds": [{"value": "company", "label": "Компания", "color": "#7c3aed"},
                                                             {"value": "department", "label": "Отдел", "color": "#4f7cff"}],
                                               "maxDepth": 2})
    assert res.status_code == 200
    a = _dep(admin, "A", kind="company")
    b = _dep(admin, "B", a["id"])
    _dep(admin, "C", b["id"], status=409)
    assert admin.put("/api/org/settings", json={"unitKinds": [{"value": "company", "label": "К"}], "maxDepth": 5}).status_code == 409
    res = admin.put("/api/org/settings", json={"unitKinds": [{"value": "Bad Code", "label": "К"}], "maxDepth": 5})
    assert res.status_code == 400


def test_only_admin_changes_structure(app, admin):
    dep = _dep(admin, "Компания", kind="company")
    editor = new_client(app, "editor1", "editor")
    assert editor.get("/api/org/tree").status_code == 200
    assert editor.post("/api/departments", json={"name": "X"}).status_code == 403
    assert editor.patch(f"/api/departments/{dep['id']}", json={"name": "Y"}).status_code == 403
    assert editor.put(f"/api/departments/{dep['id']}/heads", json={"userIds": []}).status_code == 403
    assert editor.put("/api/org/settings", json={"unitKinds": [], "maxDepth": 3}).status_code == 403


def test_department_access_covers_the_whole_branch(app, admin):
    from tests.conftest import login
    from tests.helpers import create_project

    company = _dep(admin, "Компания", kind="company")
    sales = _dep(admin, "Продажи", company["id"])
    group = _dep(admin, "Группа Север", sales["id"], kind="group")
    other = _dep(admin, "Бухгалтерия", company["id"])
    project_id, plan_id = create_project(admin, "Воронка продаж")
    member = make_user(app, "seller", "editor", name="Продавец")
    outsider = make_user(app, "accountant", "editor", name="Бухгалтер")
    admin.patch(f"/api/users/{member}", json={"departmentId": group["id"]})
    admin.patch(f"/api/users/{outsider}", json={"departmentId": other["id"]})

    res = admin.post("/api/access/departments", json={"departmentIds": [sales["id"]], "projectIds": [project_id],
                                                      "level": "edit"})
    assert res.get_json()["created"] == 1
    rules = admin.get("/api/access/departments").get_json()
    assert rules[0]["departmentPath"] == "Компания / Продажи" and rules[0]["memberCount"] == 1

    seller = app.test_client()
    login(seller, "seller")
    assert [p["title"] for p in seller.get("/api/projects").get_json()] == ["Воронка продаж"]
    assert seller.post(f"/api/plans/{plan_id}/ops", json={"ops": [{"op": "item.create", "item": {
        "key": "t1", "type": "task", "name": "Звонок", "start": "2026-05-01", "end": "2026-05-02"}}]}).status_code == 200
    accountant = app.test_client()
    login(accountant, "accountant")
    assert accountant.get("/api/projects").get_json() == []
    assert accountant.get(f"/api/plans/{plan_id}").status_code == 404

    matrix = admin.get("/api/access/effective").get_json()["matrix"]
    assert matrix[str(member)] == {str(project_id): "edit"} and matrix[str(outsider)] == {}

    # Сотрудник ушёл из отдела — доступ отдела пропал
    admin.patch(f"/api/users/{member}", json={"departmentId": other["id"]})
    assert seller.get(f"/api/plans/{plan_id}").status_code == 404
    rule_id = rules[0]["id"]
    assert admin.patch(f"/api/access/departments/{rule_id}", json={"level": "view"}).get_json()["level"] == "view"
    assert admin.post("/api/access/departments/delete", json={"ids": [rule_id]}).get_json()["deleted"] == 1


def test_review_reporting_cycles_are_rejected_on_every_path(app, admin):
    """Цикл подчинения нельзя получить ни назначением руководителей, ни переносом сотрудника или подразделения."""
    a_id = make_user(app, "aaa", "editor", name="Аня")
    b_id = make_user(app, "bbb", "editor", name="Борис")
    top = _dep(admin, "Компания", kind="company")
    x = _dep(admin, "Отдел X", top["id"])
    y = _dep(admin, "Отдел Y", top["id"])
    admin.patch(f"/api/users/{a_id}", json={"departmentId": x["id"]})
    admin.put(f"/api/departments/{x['id']}/heads", json={"userIds": [b_id]})      # Аня → Борис
    # Борис явно подчиняется Ане — цикл
    assert admin.patch(f"/api/users/{b_id}", json={"managerId": a_id}).status_code == 409
    # Борис в отделе Y, руководитель которого — Аня: Борис → Аня → Борис
    admin.put(f"/api/departments/{y['id']}/heads", json={"userIds": [a_id]})
    res = admin.patch(f"/api/users/{b_id}", json={"departmentId": y["id"]})
    assert res.status_code == 409 and "Цикл подчинения" in res.get_json()["message"]
    # Тот же цикл назначением руководителя подразделения
    admin.patch(f"/api/users/{b_id}", json={"departmentId": x["id"]})  # Борис руководит X и сидит в X → Компания
    # Нельзя вложить подразделение само в себя через двух потомков
    z = _dep(admin, "Группа Z", x["id"], kind="group")
    assert admin.patch(f"/api/departments/{x['id']}", json={"parentId": z["id"]}).status_code == 409
    tree = admin.get("/api/org/tree").get_json()
    assert {d["name"] for d in tree["departments"]} >= {"Компания", "Отдел X", "Отдел Y", "Группа Z"}


def test_review_subtree_survives_a_cycle_in_data(app, admin):
    """Даже если в базе оказался цикл подразделений (до проверок), обход структуры не зависает."""
    from app.models import Department
    from app.services.org import OrgSnapshot

    a = _dep(admin, "A", kind="company")
    b = _dep(admin, "B", a["id"])
    with app.app_context():
        db.session.get(Department, a["id"]).parent_id = b["id"]
        db.session.commit()
        snap = OrgSnapshot()
        assert set(snap.subtree(a["id"])) == {a["id"], b["id"]}
        assert snap.department_cycle() == {a["id"], b["id"]}
        db.session.get(Department, a["id"]).parent_id = None
        db.session.commit()


def test_review_employee_csv_export_neutralises_formulas(app, admin):
    user_id = make_user(app, "formula", "editor", name="=HYPERLINK(\"http://evil\")")
    assert user_id
    text = admin.get("/api/users/export?format=csv").get_data(as_text=True)
    assert "'=HYPERLINK" in text
