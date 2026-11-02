"""Статистика плана с главной (0.4): данные окна «Статистика» без открытия плана и сроки на карточке проекта."""

from app.extensions import db
from app.models import PlanPresence, User
from tests.conftest import new_client
from tests.helpers import create_project, ok_ops, task


def _uid(app, login_name):
    with app.app_context():
        return db.session.query(User).filter_by(login=login_name).one().id


def _presence_count(app, plan_id):
    with app.app_context():
        return db.session.query(PlanPresence).filter_by(plan_id=plan_id).count()


def test_plan_stats_returns_rows_for_statistics(app, admin):
    project_id, plan_id = create_project(admin, "Статистика с главной")
    ok_ops(admin, plan_id, [
        {"op": "item.create", "item": {"key": "g1", "type": "group", "name": "Этап"}},
        task("t1", "Анализ", status="done", progress=100, parentKey="g1", assigneeKey="a1", jiraUrl="https://jira/browse/P-1"),
        task("t2", "Разработка", status="inwork", progress=40, offset=3, end="2026-05-20"),
        task("t3", "Удалённая"),
        {"op": "item.create", "item": {"key": "m1", "type": "milestone", "name": "Сдача", "date": "2099-01-01"}},
        {"op": "assignee.create", "assignee": {"key": "a1", "name": "Петров Пётр", "position": 1}},
    ])
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "t3"}])
    response = admin.get(f"/api/plans/{plan_id}/stats")
    assert response.status_code == 200
    data = response.get_json()
    assert data["plan"] == {"id": plan_id, "title": "Статистика с главной", "projectId": project_id,
                            "projectTitle": "Статистика с главной", "isArchived": False, "access": "manage"}
    items = {i["key"]: i for i in data["items"]}
    assert set(items) == {"g1", "t1", "t2", "m1"}
    assert items["t2"] == {"key": "t2", "type": "task", "name": "Разработка", "parentKey": None, "position": items["t2"]["position"],
                           "assigneeKey": None, "status": "inwork", "start": "2026-05-01", "end": "2026-05-20",
                           "progress": 40, "offset": 3}
    assert items["t1"]["parentKey"] == "g1" and "jiraUrl" not in items["t1"]
    assert items["m1"]["date"] == "2099-01-01"
    assert data["assignees"] == [{"key": "a1", "name": "Петров Пётр", "position": 1}]


def test_plan_stats_checks_access_and_does_not_mark_presence(app, admin):
    project_id, plan_id = create_project(admin, "Закрытый")
    ok_ops(admin, plan_id, [task("t1")])
    outsider = new_client(app, "outsider", "editor")
    assert outsider.get(f"/api/plans/{plan_id}/stats").status_code == 404
    viewer = new_client(app, "viewer2", "viewer")
    admin.post("/api/access", json={"userIds": [_uid(app, "viewer2")], "projectIds": [project_id], "level": "view"})
    before = _presence_count(app, plan_id)
    response = viewer.get(f"/api/plans/{plan_id}/stats")
    assert response.status_code == 200
    assert response.get_json()["plan"]["access"] == "view"
    # Статистику смотрят с главной: «сейчас на плане» сотрудник не появляется
    assert _presence_count(app, plan_id) == before
    # План в корзине недоступен
    admin.post(f"/api/projects/{project_id}/plans", json={"title": "Второй"})
    admin.delete(f"/api/plans/{plan_id}")
    assert viewer.get(f"/api/plans/{plan_id}/stats").status_code == 404


def test_dashboard_stats_include_plan_dates(app, admin):
    create_project(admin, "Пустой")
    _project_id, plan_id = create_project(admin, "Сроки")
    ok_ops(admin, plan_id, [
        task("t1", start="2026-03-02", end="2026-04-10"), task("t2", start="2026-04-01", end="2026-06-30"),
        {"op": "item.create", "item": {"key": "m1", "type": "milestone", "name": "Позже задач", "date": "2026-12-01"}},
    ])
    projects = {p["title"]: p for p in admin.get("/api/projects").get_json()}
    stats = projects["Сроки"]["stats"]
    assert stats["start"] == "2026-03-02" and stats["end"] == "2026-06-30"
    # Проект без задач — без сроков (и без stats, как раньше)
    assert projects["Пустой"].get("stats") is None


def test_v061_card_shows_every_open_plan_and_skips_an_empty_first_tab(app, admin):
    """0.6.1: пустая первая вкладка не оставляет карточку без данных; у каждого открытого плана — своя сводка."""
    project_id, first_plan = create_project(admin, "Этапы")
    second = admin.post(f"/api/projects/{project_id}/plans", json={"title": "Этап 2"}).get_json()["id"]
    third = admin.post(f"/api/projects/{project_id}/plans", json={"title": "Этап 3"}).get_json()["id"]
    ok_ops(admin, second, [task("a", status="inwork", progress=50, start="2026-03-02", end="2026-03-20"),
                           task("b", status="done", progress=100, start="2026-03-01", end="2026-03-05")])
    ok_ops(admin, third, [task("c", status="blocked", start="2026-05-01", end="2026-05-10")])
    archived = admin.post(f"/api/projects/{project_id}/plans", json={"title": "Старый"}).get_json()["id"]
    ok_ops(admin, archived, [task("x", start="2026-01-01", end="2026-01-02")])
    admin.post(f"/api/plans/{archived}/archive")

    project = next(p for p in admin.get("/api/projects").get_json() if p["title"] == "Этапы")
    # Сводка — по первой вкладке с задачами («Этап 2»), а не по пустой первой
    assert project["stats"]["planId"] == second and project["stats"]["planTitle"] == "Этап 2"
    assert (project["stats"]["total"], project["stats"]["done"], project["stats"]["inwork"]) == (2, 1, 1)
    # Все открытые планы по порядку вкладок, у каждого — своя сводка; архивный не показывается
    plans = project["plans"]
    assert [p["id"] for p in plans] == [first_plan, second, third]
    assert plans[0]["stats"]["total"] == 0
    assert (plans[1]["stats"]["total"], plans[1]["stats"]["progress"]) == (2, 75)
    assert (plans[2]["stats"]["total"], plans[2]["stats"]["blocked"], plans[2]["stats"]["end"]) == (1, 1, "2026-05-10")
    # Статистика основного плана с главной — та же вкладка
    assert admin.get(f"/api/plans/{second}/stats").status_code == 200
