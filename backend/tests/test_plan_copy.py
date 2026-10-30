"""Копия плана: строки, исполнители, пометки и обсуждения строк."""

from tests.helpers import create_project, ok_ops, task


def _comments(client, plan_id, key):
    res = client.get(f"/api/plans/{plan_id}/items/{key}/comments")
    assert res.status_code == 200
    return res.get_json()


def test_copy_keeps_comments_replies_and_counts(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1", "Первая"), task("t2", "Вторая")])
    url = f"/api/plans/{plan_id}/items/t1/comments"
    first = admin.post(url, json={"text": "Вопрос"}).get_json()["comment"]
    removed = admin.post(url, json={"text": "Ошибочный ответ"}).get_json()["comment"]
    admin.post(url, json={"text": "Ответ на ответ", "replyToId": removed["id"]})
    admin.post(url, json={"text": "Ответ", "replyToId": first["id"]})
    assert admin.delete(f"/api/comments/{removed['id']}").status_code == 200

    copy = admin.post(f"/api/projects/{project_id}/plans", json={"sourcePlanId": plan_id}).get_json()
    copied = _comments(admin, copy["id"], "t1")
    original = _comments(admin, plan_id, "t1")
    assert [(c["text"], c["deleted"], c["authorName"], c["createdAt"]) for c in copied] == \
        [(c["text"], c["deleted"], c["authorName"], c["createdAt"]) for c in original]
    assert {c["id"] for c in copied}.isdisjoint({c["id"] for c in original})
    by_text = {c["text"]: c for c in copied}
    deleted = next(c for c in copied if c["deleted"])
    assert by_text["Ответ"]["replyToId"] == by_text["Вопрос"]["id"]
    assert by_text["Ответ на ответ"]["replyToId"] == deleted["id"]
    assert _comments(admin, copy["id"], "t2") == []

    items = {i["key"]: i for i in admin.get(f"/api/plans/{copy['id']}").get_json()["items"]}
    assert items["t1"]["commentCount"] == 3 and items["t2"]["commentCount"] == 0

    # Обсуждения независимы: правка в копии не меняет оригинал
    admin.post(f"/api/plans/{copy['id']}/items/t1/comments", json={"text": "Только в копии"})
    assert len(_comments(admin, plan_id, "t1")) == 4


def test_copy_does_not_carry_comments_of_deleted_rows(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("t1"), task("t2")])
    admin.post(f"/api/plans/{plan_id}/items/t2/comments", json={"text": "К удалённой строке"})
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "t2"}])
    copy = admin.post(f"/api/projects/{project_id}/plans", json={"sourcePlanId": plan_id}).get_json()
    assert _comments(admin, copy["id"], "t2") == []
