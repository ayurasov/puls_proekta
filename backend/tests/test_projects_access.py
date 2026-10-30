from app.extensions import db
from app.models import User
from tests.conftest import make_user, new_client
from tests.helpers import create_project


def _uid(app, login_name):
    with app.app_context():
        return db.session.query(User).filter_by(login=login_name).one().id


def test_project_visibility_follows_access_rules(app, admin):
    project_id, plan_id = create_project(admin, "Секретный")
    viewer = new_client(app, "viewer1", "viewer")
    assert viewer.get("/api/projects").get_json() == []
    assert viewer.get(f"/api/projects/{project_id}").status_code == 404
    assert viewer.get(f"/api/plans/{plan_id}").status_code == 404
    uid = _uid(app, "viewer1")
    response = admin.post("/api/access", json={"userIds": [uid], "projectIds": [project_id], "level": "edit"})
    assert response.get_json()["created"] == 1
    projects = viewer.get("/api/projects").get_json()
    # Наблюдатель ограничен просмотром, даже если правило даёт редактирование
    assert projects[0]["access"] == "view"
    assert viewer.post(f"/api/plans/{plan_id}/ops", json={"ops": [{"op": "plan.update", "fields": {"title": "x"}}]}) \
        .status_code == 403


def test_bulk_grant_does_not_duplicate_rules(app, admin):
    p1, _ = create_project(admin, "A")
    p2, _ = create_project(admin, "B")
    make_user(app, "u1", "editor")
    make_user(app, "u2", "editor")
    ids = [_uid(app, "u1"), _uid(app, "u2")]
    first = admin.post("/api/access", json={"userIds": ids, "projectIds": [p1, p2], "level": "view"}).get_json()
    assert first == {"created": 4, "updated": 0, "unchanged": 0}
    again = admin.post("/api/access", json={"userIds": ids, "projectIds": [p1, p2], "level": "view"}).get_json()
    assert again == {"created": 0, "updated": 0, "unchanged": 4}
    raised = admin.post("/api/access", json={"userIds": ids, "projectIds": [p1], "level": "edit"}).get_json()
    assert raised["updated"] == 2
    assert len(admin.get("/api/access").get_json()) == 4


def test_direction_rule_grants_all_projects_of_direction(app, admin):
    direction = admin.post("/api/directions", json={"name": "Внутренние продукты"}).get_json()
    p1, _ = create_project(admin, "Продукт 1", direction["id"])
    p2, _ = create_project(admin, "Продукт 2", None)
    editor = new_client(app, "ed", "editor")
    admin.post("/api/access", json={"userIds": [_uid(app, "ed")], "directionIds": [direction["id"]], "level": "edit"})
    visible = {p["id"]: p["access"] for p in editor.get("/api/projects").get_json()}
    assert visible == {p1: "edit"}
    assert p2 not in visible


def test_manager_creates_and_manages_own_project(app, admin):
    manager = new_client(app, "mgr", "manager")
    project_id, _ = create_project(manager, "Мой проект")
    projects = manager.get("/api/projects").get_json()
    assert projects[0]["access"] == "manage"
    editor = new_client(app, "ed2", "editor")
    assert editor.post("/api/projects", json={"title": "Нельзя"}).status_code == 403
    assert manager.patch(f"/api/projects/{project_id}", json={"title": "Новое имя"}).status_code == 200


def test_project_delete_goes_to_trash_and_restores(app, admin):
    project_id, plan_id = create_project(admin, "В корзину")
    assert admin.delete(f"/api/projects/{project_id}").status_code == 200
    assert admin.get(f"/api/projects/{project_id}").status_code == 404
    trash = admin.get("/api/trash").get_json()
    assert [p["id"] for p in trash["projects"]] == [project_id]
    assert admin.post(f"/api/projects/{project_id}/restore").status_code == 200
    assert admin.get(f"/api/plans/{plan_id}").status_code == 200
    admin.delete(f"/api/projects/{project_id}")
    assert admin.delete(f"/api/trash/projects/{project_id}").status_code == 200
    assert admin.get("/api/trash").get_json()["projects"] == []


def test_direction_delete_moves_projects_to_no_direction(app, admin):
    direction = admin.post("/api/directions", json={"name": "Временное"}).get_json()
    project_id, _ = create_project(admin, "П", direction["id"])
    result = admin.delete(f"/api/directions/{direction['id']}").get_json()
    assert result["projectsUnassigned"] == 1
    assert admin.get(f"/api/projects/{project_id}").get_json()["directionId"] is None


def test_dashboard_stats(app, admin):
    from tests.helpers import ok_ops, task

    _project_id, plan_id = create_project(admin, "Статистика")
    ok_ops(admin, plan_id, [
        task("t1", status="done"), task("t2", status="inwork", end="2099-05-05"), task("t3", start="2020-01-01", end="2020-01-02"),
        {"op": "item.create", "item": {"key": "m1", "type": "milestone", "name": "Сдача", "date": "2099-01-01"}},
    ])
    stats = admin.get("/api/projects").get_json()[0]["stats"]
    assert stats["total"] == 3 and stats["done"] == 1 and stats["inwork"] == 1 and stats["overdue"] == 1
    assert stats["nextMilestone"] == {"name": "Сдача", "date": "2099-01-01"}


def test_dashboard_stats_count_default_plan_only(app, admin):
    """Копия плана не удваивает задачи на карточке; процент — как «Средний прогресс» в «Статистике»."""
    from tests.helpers import ok_ops, task

    project_id, plan_id = create_project(admin, "Копии")
    ok_ops(admin, plan_id, [
        task("t1", status="done", progress=100), task("t2", progress=50, end="2099-05-05"),
        task("t3", progress=0, end="2099-05-05"),
    ])
    copy = admin.post(f"/api/projects/{project_id}/plans", json={"sourcePlanId": plan_id}).get_json()
    ok_ops(admin, copy["id"], [task("t9", progress=10, end="2099-05-05")])
    stats = admin.get("/api/projects").get_json()[0]["stats"]
    assert stats["total"] == 3 and stats["done"] == 1
    assert stats["progress"] == 50  # (100 + 50 + 0) / 3
    assert stats["planId"] == plan_id
    # Основной план — первая открытая вкладка: после переноса копии вперёд считается копия
    admin.post(f"/api/projects/{project_id}/plans/reorder", json={"ids": [copy["id"], plan_id]})
    stats = admin.get("/api/projects").get_json()[0]["stats"]
    assert stats["planId"] == copy["id"] and stats["total"] == 4
    # Архивный план не считается основным
    admin.post(f"/api/plans/{copy['id']}/archive")
    stats = admin.get("/api/projects").get_json()[0]["stats"]
    assert stats["planId"] == plan_id and stats["total"] == 3
