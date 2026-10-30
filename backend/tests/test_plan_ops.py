"""Операции над планом: поля, журнал, конфликты, целостность, снимки, синхронизация."""

from tests.conftest import new_client
from tests.helpers import create_project, ok_ops, ops, task
from app.extensions import db
from app.models import User


def _grant(app, admin, login_name, project_id, level="edit"):
    with app.app_context():
        uid = db.session.query(User).filter_by(login=login_name).one().id
    admin.post("/api/access", json={"userIds": [uid], "projectIds": [project_id], "level": level})


def test_create_update_delete_restore_with_history(app, admin):
    _, plan_id = create_project(admin)
    result = ok_ops(admin, plan_id, [task("t1", "Анализ"), task("t2", "Разработка")])
    assert result["revision"] == 1 and len(result["items"]) == 2
    result = ok_ops(admin, plan_id, [{"op": "item.update", "key": "t1", "fields": {"status": "inwork", "end": "2026-05-10"}}])
    item = result["items"][0]
    assert item["status"] == "inwork" and item["end"] == "2026-05-10" and item["start"] == "2026-05-01"
    events = admin.get(f"/api/plans/{plan_id}/events?entityKey=t1").get_json()["events"]
    summary = events[0]["summary"]
    assert "Сроки: 01.05.2026–05.05.2026 → 01.05.2026–10.05.2026" in summary
    assert "Статус: Новая → В работе" in summary
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "t2"}])
    doc = admin.get(f"/api/plans/{plan_id}").get_json()
    assert [i["key"] for i in doc["items"]] == ["t1"]
    ok_ops(admin, plan_id, [{"op": "item.restore", "key": "t2"}])
    doc = admin.get(f"/api/plans/{plan_id}").get_json()
    assert {i["key"] for i in doc["items"]} == {"t1", "t2"}


def test_noop_update_does_not_bump_revision(app, admin):
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", "Анализ")])
    result = ok_ops(admin, plan_id, [{"op": "item.update", "key": "t1", "fields": {"name": "Анализ"}}])
    assert result["revision"] == 1


def test_validation_rejects_whole_batch(app, admin):
    _, plan_id = create_project(admin)
    response = ops(admin, plan_id, [task("t1"), task("t2", start="2026-05-10", end="2026-05-01")])
    assert response.status_code == 400
    assert "раньше начала" in response.get_json()["message"]
    assert admin.get(f"/api/plans/{plan_id}").get_json()["items"] == []
    response = ops(admin, plan_id, [task("t3", jiraUrl="javascript:alert(1)")])
    assert response.status_code == 400


def test_concurrent_edits_of_different_fields_both_survive(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", "Общая задача")])
    editor = new_client(app, "colleague", "editor", name="Коллега")
    _grant(app, admin, "colleague", project_id)
    # Оба открыли план на ревизии 1
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "t1", "fields": {"status": "done"}}], base=1)
    result = ok_ops(editor, plan_id, [{"op": "item.update", "key": "t1", "fields": {"progress": 40}}], base=1)
    # Коллега узнаёт, что строку только что менял другой сотрудник
    assert result["conflicts"] and result["conflicts"][0]["byName"] == "Администратор"
    item = [i for i in admin.get(f"/api/plans/{plan_id}").get_json()["items"] if i["key"] == "t1"][0]
    assert item["status"] == "done" and item["progress"] == 40


def test_changes_polling_and_presence(app, admin):
    project_id, plan_id = create_project(admin)
    editor = new_client(app, "colleague", "editor", name="Коллега")
    _grant(app, admin, "colleague", project_id)
    base = editor.get(f"/api/plans/{plan_id}/changes?since=0&editing=1").get_json()["revision"]
    ok_ops(admin, plan_id, [task("t1", "Новая задача")])
    changes = editor.get(f"/api/plans/{plan_id}/changes?since={base}").get_json()
    assert [i["key"] for i in changes["items"]] == ["t1"]
    assert changes["events"][0]["userName"] == "Администратор"
    presence = admin.get(f"/api/plans/{plan_id}/changes?since={changes['revision']}").get_json()
    assert presence["unchanged"] is True
    assert [p["name"] for p in presence["presence"]] == ["Коллега"]
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "t1"}])
    deleted = editor.get(f"/api/plans/{plan_id}/changes?since={changes['revision']}").get_json()
    assert deleted["items"][0]["deleted"] is True


def test_integrity_fixes_dangling_and_cyclic_references(app, admin):
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [
        {"op": "item.create", "item": {"key": "g1", "type": "group", "name": "Группа"}},
        task("t1", parentKey="g1"), task("t2", dependencyKey="t1"),
        {"op": "note.create", "note": {"key": "n1", "date": "2026-05-02", "text": "Пометка", "rowKey": "t1"}},
    ])
    result = ok_ops(admin, plan_id, [{"op": "item.delete", "key": "t1"}, {"op": "item.delete", "key": "g1"}])
    fixes = {(f["key"], f["field"]) for f in result["fixes"]}
    assert ("t2", "dependencyKey") in fixes and ("n1", "rowKey") in fixes
    doc = admin.get(f"/api/plans/{plan_id}").get_json()
    assert doc["items"][0]["dependencyKey"] is None and doc["notes"] == []
    ok_ops(admin, plan_id, [task("a"), task("b", parentKey="a")])
    result = ok_ops(admin, plan_id, [{"op": "item.update", "key": "a", "fields": {"parentKey": "b"}}])
    assert any(f["field"] == "parentKey" for f in result["fixes"])


def test_viewer_and_archived_plan_are_read_only(app, admin):
    project_id, plan_id = create_project(admin)
    viewer = new_client(app, "v", "editor")
    _grant(app, admin, "v", project_id, "view")
    assert viewer.get(f"/api/plans/{plan_id}").get_json()["plan"]["access"] == "view"
    assert ops(viewer, plan_id, [task("x")]).status_code == 403
    second = admin.post(f"/api/projects/{project_id}/plans", json={"title": "Вторая версия"}).get_json()
    assert admin.post(f"/api/plans/{second['id']}/archive").status_code == 200
    assert ops(admin, second["id"], [task("x")]).status_code == 409
    # Единственный открытый план в архив не убрать
    assert admin.post(f"/api/plans/{plan_id}/archive").status_code == 400


def test_plan_delete_requires_archive_and_goes_to_trash(app, admin):
    project_id, plan_id = create_project(admin)
    second = admin.post(f"/api/projects/{project_id}/plans", json={"title": "Черновик"}).get_json()
    assert admin.delete(f"/api/plans/{second['id']}").status_code == 409
    admin.post(f"/api/plans/{second['id']}/archive")
    assert admin.delete(f"/api/plans/{second['id']}").status_code == 200
    trash = admin.get("/api/trash").get_json()
    assert [p["id"] for p in trash["plans"]] == [second["id"]]
    assert admin.post(f"/api/plans/{second['id']}/restore").status_code == 200


def test_duplicate_plan_copies_content(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [
        {"op": "assignee.create", "assignee": {"key": "a1", "name": "Иванов", "role": "Аналитик"}},
        task("t1", assigneeKey="a1"),
        {"op": "note.create", "note": {"key": "n1", "date": "2026-05-02", "text": "Важно"}},
    ])
    copy = admin.post(f"/api/projects/{project_id}/plans", json={"sourcePlanId": plan_id}).get_json()
    assert copy["title"].endswith("(копия)")
    doc = admin.get(f"/api/plans/{copy['id']}").get_json()
    assert doc["items"][0]["assigneeKey"] == "a1" and doc["assignees"][0]["name"] == "Иванов"
    assert doc["notes"][0]["text"] == "Важно"


def test_snapshots_and_restore(app, admin):
    app.config["SNAPSHOT_INTERVAL_MINUTES"] = 0
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", "Первая")])
    ok_ops(admin, plan_id, [task("t2", "Вторая")])  # перед этой правкой — автоснимок с одной задачей
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "t1", "fields": {"name": "Переименована"}}])
    snapshots = admin.get(f"/api/plans/{plan_id}/snapshots").get_json()
    first = [s for s in snapshots if s["itemsCount"] == 1][0]
    result = admin.post(f"/api/plans/{plan_id}/snapshots/{first['id']}/restore").get_json()
    assert result["changes"] == 2
    doc = admin.get(f"/api/plans/{plan_id}").get_json()
    assert [(i["key"], i["name"]) for i in doc["items"]] == [("t1", "Первая")]
    reasons = {s["reason"] for s in admin.get(f"/api/plans/{plan_id}/snapshots").get_json()}
    assert "before_restore" in reasons


def test_comments_count_and_permissions(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1")])
    editor = new_client(app, "author", "editor", name="Автор")
    _grant(app, admin, "author", project_id, "edit")
    created = editor.post(f"/api/plans/{plan_id}/items/t1/comments", json={"text": "Первый"}).get_json()
    assert created["commentCount"] == 1
    reply = admin.post(f"/api/plans/{plan_id}/items/t1/comments",
                       json={"text": "Ответ", "replyToId": created["comment"]["id"]}).get_json()
    assert reply["comment"]["replyToId"] == created["comment"]["id"]
    other = new_client(app, "other", "editor")
    _grant(app, admin, "other", project_id, "edit")
    assert other.delete(f"/api/comments/{created['comment']['id']}").status_code == 403
    assert editor.delete(f"/api/comments/{created['comment']['id']}").status_code == 200
    comments = admin.get(f"/api/plans/{plan_id}/items/t1/comments").get_json()
    assert [c["deleted"] for c in comments] == [True, False]
    item = admin.get(f"/api/plans/{plan_id}").get_json()["items"][0]
    assert item["commentCount"] == 1


def test_plan_settings_are_validated(app, admin):
    _, plan_id = create_project(admin)
    response = admin.patch(f"/api/plans/{plan_id}", json={"settings": {"range": {"start": "2026-12-01", "end": "2026-01-01"}}})
    assert response.status_code == 400
    response = admin.patch(f"/api/plans/{plan_id}", json={"settings": {"scale": "week", "display": {"showNotes": False}}})
    settings = response.get_json()["settings"]
    assert settings["scale"] == "week" and settings["display"]["showNotes"] is False
    assert settings["display"]["showProgress"] is True
