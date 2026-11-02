"""0.7.0: связи плана — отдельная сущность (ADR-0012, 5.1), крайний срок и ограничение «не ранее» (5.4).

Операции link.*, проверки (строки, повтор, цикл), целостность при удалении и восстановлении строк, история,
повтор пакета, поля предшественника до 0.7.0, снимки (и снимки до 0.7.0), копия плана, JSON 1.0, API интеграций,
миграция 0007 с данными.
"""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests.conftest import new_client
from tests.helpers import create_project, ok_ops, ops, task
from app.extensions import db
from app.models import PlanSnapshot, User

BACKEND = Path(__file__).resolve().parents[1]


def link(key, frm, to, **fields):
    return {"op": "link.create", "link": {"key": key, "from": frm, "to": to, **fields}}


def milestone(key, name="Веха", date="2026-05-20", **extra):
    return {"op": "item.create", "item": {"key": key, "type": "milestone", "name": name, "date": date, **extra}}


def _doc(client, plan_id):
    return client.get(f"/api/plans/{plan_id}").get_json()


def _links(client, plan_id):
    return {(link["from"], link["to"]): link for link in _doc(client, plan_id)["links"]}


def _events(client, plan_id, key):
    return [e["summary"] for e in client.get(f"/api/plans/{plan_id}/events?entityKey={key}").get_json()["events"]]


def _plan_with_rows(admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [
        task("a", "Анализ"), task("b", "Разработка", start="2026-05-06", end="2026-05-10"),
        task("c", "Тесты", start="2026-05-11", end="2026-05-12"), milestone("m", "Сдача"),
        {"op": "item.create", "item": {"key": "g", "type": "group", "name": "Этап"}},
    ])
    return project_id, plan_id


def test_link_create_update_delete_restore_with_history(app, admin):
    _, plan_id = _plan_with_rows(admin)
    result = ok_ops(admin, plan_id, [link("l1", "a", "b", type="FS", lag=2)])
    assert result["links"] == [{"key": "l1", "from": "a", "to": "b", "type": "FS", "lag": 2, "lagKind": "calendar",
                                "updatedRev": result["revision"]}]
    assert "Связь «Анализ» → «Разработка» (окончание → начало, +2 дн.) добавлена" in _events(admin, plan_id, "b")

    ok_ops(admin, plan_id, [{"op": "link.update", "key": "l1", "fields": {"type": "SS", "lag": -1, "lagKind": "working"}}])
    assert _links(admin, plan_id)[("a", "b")]["lagKind"] == "working"
    summary = _events(admin, plan_id, "b")[0]
    assert summary == ("Связь «Анализ» → «Разработка»: тип «окончание → начало» → «начало → начало»; "
                       "лаг +2 дн. → -1 раб. дн.")
    # Правка без изменений не двигает ревизию
    same = ok_ops(admin, plan_id, [{"op": "link.update", "key": "l1", "fields": {"type": "SS"}}])
    assert same["links"] == [] and same["revision"] == result["revision"] + 1

    ok_ops(admin, plan_id, [{"op": "link.delete", "key": "l1"}])
    assert _links(admin, plan_id) == {}
    assert _events(admin, plan_id, "b")[0] == "Связь «Анализ» → «Разработка» (начало → начало, -1 раб. дн.) удалена"
    ok_ops(admin, plan_id, [{"op": "link.restore", "key": "l1"}])
    assert set(_links(admin, plan_id)) == {("a", "b")}
    assert _events(admin, plan_id, "b")[0].endswith("восстановлена")
    # Удалённую связь нельзя изменить
    ok_ops(admin, plan_id, [{"op": "link.delete", "key": "l1"}])
    gone = ops(admin, plan_id, [{"op": "link.update", "key": "l1", "fields": {"lag": 3}}])
    assert gone.status_code == 409


def test_link_validation(app, admin):
    _, plan_id = _plan_with_rows(admin)
    ok_ops(admin, plan_id, [task("x", "Удалённая")])
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "x"}])
    cases = [
        (link("l1", "a", "zz"), 400, "не найден"),
        (link("l1", "a", "a"), 400, "самой себя"),
        (link("l1", "a", "g"), 400, "группы не связываются"),
        (link("l1", "g", "a"), 400, "группы не связываются"),
        (link("l1", "a", "x"), 409, "удален"),
        (link("l1", "a", "b", type="XX"), 400, "тип связи"),
        (link("l1", "a", "b", lagKind="hours"), 400, "рабочих или календарных"),
        (link("l1", "a", "b", lag=1.5), 400, "целым числом"),
        (link("l1", "a", "b", lag=99999), 400, "целым числом"),
        ({"op": "link.create", "link": {"key": "l1", "from": "a"}}, 400, "Укажите"),
        ({"op": "link.create", "link": {"key": "плохой ключ", "from": "a", "to": "b"}}, 400, "Неверный"),
    ]
    for operation, status, message in cases:
        response = ops(admin, plan_id, [operation])
        assert response.status_code == status, (operation, response.get_json())
        assert message in response.get_json()["message"], response.get_json()
    ok_ops(admin, plan_id, [link("l1", "a", "b")])
    duplicate = ops(admin, plan_id, [link("l2", "a", "b", type="SS")])
    assert duplicate.status_code == 400 and "уже есть" in duplicate.get_json()["message"]
    # Последующей может быть веха, предшественником — тоже
    ok_ops(admin, plan_id, [link("l3", "b", "m"), link("l4", "m", "c", type="SS")])
    assert {("a", "b"), ("b", "m"), ("m", "c")} == set(_links(admin, plan_id))


def test_cycles_are_rejected_on_create_update_and_restore(app, admin):
    _, plan_id = _plan_with_rows(admin)
    ok_ops(admin, plan_id, [link("ab", "a", "b"), link("bc", "b", "c")])
    response = ops(admin, plan_id, [link("ca", "c", "a")])
    assert response.status_code == 400 and response.get_json()["error"] == "link_cycle"
    assert response.get_json()["message"] == ("Нельзя связать «Тесты» → «Анализ»: «Анализ» уже предшествует «Тесты» "
                                              "(напрямую или через цепочку связей) — получился бы цикл")
    # Цикл через правку конца связи
    response = ops(admin, plan_id, [{"op": "link.update", "key": "bc", "fields": {"to": "a"}}])
    assert response.status_code == 400 and response.get_json()["error"] == "link_cycle"
    assert ops(admin, plan_id, [{"op": "link.update", "key": "bc", "fields": {"from": "c"}}]).status_code == 400
    # Пакет отклоняется целиком: связи из начала пакета не остаются
    assert ops(admin, plan_id, [link("am", "a", "m"), link("ca", "c", "a")]).status_code == 400
    assert set(_links(admin, plan_id)) == {("a", "b"), ("b", "c")}
    # Правка конца связи без цикла — разрешена
    ok_ops(admin, plan_id, [{"op": "link.update", "key": "bc", "fields": {"to": "m"}}])
    assert set(_links(admin, plan_id)) == {("a", "b"), ("b", "m")}
    assert "последующая «Тесты» → «Сдача»" in _events(admin, plan_id, "m")[0]
    # Восстановление связи, которая теперь замкнула бы цикл, не отклоняет пакет: связь остаётся удалённой
    ok_ops(admin, plan_id, [{"op": "link.delete", "key": "ab"}, link("ba", "b", "a")])
    result = ok_ops(admin, plan_id, [{"op": "link.restore", "key": "ab"}])
    assert result["fixes"][0]["entity"] == "link" and "цикл" in result["fixes"][0]["reason"]
    assert result["links"][0]["deleted"] is True
    assert set(_links(admin, plan_id)) == {("b", "a"), ("b", "m")}


def test_links_follow_row_delete_and_restore(app, admin):
    _, plan_id = _plan_with_rows(admin)
    ok_ops(admin, plan_id, [link("ab", "a", "b"), link("bc", "b", "c"), link("am", "a", "m")])
    # Связь, удалённая до строки, со строкой не возвращается
    ok_ops(admin, plan_id, [{"op": "link.delete", "key": "am"}])
    result = ok_ops(admin, plan_id, [{"op": "item.delete", "key": "b"}])
    fixes = {(f["entity"], f["key"], f["field"]) for f in result["fixes"]}
    assert fixes == {("link", "ab", "to"), ("link", "bc", "from")}
    assert _links(admin, plan_id) == {}
    result = ok_ops(admin, plan_id, [{"op": "item.restore", "key": "b"}])
    assert {link["key"] for link in result["links"]} == {"ab", "bc"}
    assert set(_links(admin, plan_id)) == {("a", "b"), ("b", "c")}
    assert "Связь «Анализ» → «Разработка» (окончание → начало) восстановлена вместе со строкой" in _events(admin, plan_id, "b")

    # Удалены обе строки связи: связь возвращается, когда восстановлены обе
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "c"}])
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "b"}])
    ok_ops(admin, plan_id, [{"op": "item.restore", "key": "b"}])
    assert set(_links(admin, plan_id)) == {("a", "b")}
    ok_ops(admin, plan_id, [{"op": "item.restore", "key": "c"}])
    assert set(_links(admin, plan_id)) == {("a", "b"), ("b", "c")}

    # Пока строка удалена, появилась встречная цепочка — связь, замыкающая цикл, не возвращается
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "b"}])
    ok_ops(admin, plan_id, [link("ca", "c", "a")])
    ok_ops(admin, plan_id, [{"op": "item.restore", "key": "b"}])
    assert set(_links(admin, plan_id)) == {("c", "a"), ("a", "b")}

    # Клиент сам удаляет связи вместе со строкой и сам возвращает их отменой — без исправлений сервера
    ok_ops(admin, plan_id, [{"op": "link.delete", "key": "ab"}, {"op": "item.delete", "key": "a"},
                            {"op": "link.delete", "key": "ca"}])
    result = ok_ops(admin, plan_id, [{"op": "item.restore", "key": "a"}, {"op": "link.restore", "key": "ab"},
                                     {"op": "link.restore", "key": "ca"}])
    assert result["fixes"] == []
    assert set(_links(admin, plan_id)) == {("c", "a"), ("a", "b")}


def test_replayed_link_create_is_idempotent_for_its_author(app, admin):
    project_id, plan_id = _plan_with_rows(admin)
    batch = [link("l1", "a", "b", lag=1)]
    first = ok_ops(admin, plan_id, batch)
    again = ok_ops(admin, plan_id, batch)
    assert again["revision"] == first["revision"]
    assert len(_doc(admin, plan_id)["links"]) == 1
    colleague = new_client(app, "colleague", "editor")
    with app.app_context():
        uid = db.session.query(User).filter_by(login="colleague").one().id
    admin.post("/api/access", json={"userIds": [uid], "projectIds": [project_id], "level": "edit"})
    assert ops(colleague, plan_id, batch).status_code == 409
    # Правка связи коллегой после базовой ревизии — конфликт поля, как у строк
    ok_ops(admin, plan_id, [{"op": "link.update", "key": "l1", "fields": {"lag": 3}}])
    result = ok_ops(colleague, plan_id, [{"op": "link.update", "key": "l1", "fields": {"type": "SS"}}], base=first["revision"])
    assert result["conflicts"] == [{"entity": "link", "key": "l1", "byId": result["conflicts"][0]["byId"],
                                    "byName": "Администратор"}]


def test_legacy_dependency_fields_become_links(app, admin):
    """Поля предшественника до 0.7.0 в операциях строк (документы, скрипты, страница до обновления) — связи."""
    _, plan_id = create_project(admin)
    # Предшественник идёт в пакете позже последующей строки; ссылка на отсутствующую строку снимается, как раньше
    result = ok_ops(admin, plan_id, [
        task("b", "B", dependencyKey="a", dependencyType="SS", dependencyLag=2),
        task("a", "A"), task("c", "C", dependencyKey="zz"),
    ])
    assert [(f["key"], f["field"]) for f in result["fixes"]] == [("c", "dependencyKey")]
    assert {k: (v["type"], v["lag"]) for k, v in _links(admin, plan_id).items()} == {("a", "b"): ("SS", 2)}
    doc = _doc(admin, plan_id)
    assert all("dependencyKey" not in item for item in doc["items"])
    # Правка единственного предшественника
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "b", "fields": {"dependencyLag": 5}}])
    assert _links(admin, plan_id)[("a", "b")]["lag"] == 5
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "b", "fields": {"dependencyKey": "c"}}])
    assert set(_links(admin, plan_id)) == {("c", "b")}
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "b", "fields": {"dependencyKey": None}}])
    assert _links(admin, plan_id) == {}
    # Тип связи — только из списка
    assert ops(admin, plan_id, [{"op": "item.update", "key": "b", "fields": {"dependencyType": "XX"}}]).status_code == 400
    # Несколько предшественников — правка одним полем неоднозначна
    ok_ops(admin, plan_id, [link("l1", "a", "b"), link("l2", "c", "b")])
    response = ops(admin, plan_id, [{"op": "item.update", "key": "b", "fields": {"dependencyKey": None}}])
    assert response.status_code == 400 and "несколько предшественников" in response.get_json()["message"]


def test_deadline_and_constraint(app, admin):
    _, plan_id = _plan_with_rows(admin)
    result = ok_ops(admin, plan_id, [
        {"op": "item.update", "key": "a", "fields": {"deadline": "2026-05-04", "constraintType": "snet",
                                                    "constraintDate": "2026-05-01"}},
        {"op": "item.update", "key": "m", "fields": {"deadline": "2026-05-25"}},
        {"op": "item.update", "key": "g", "fields": {"deadline": "2026-05-25"}},
    ])
    items = {i["key"]: i for i in _doc(admin, plan_id)["items"]}
    assert (items["a"]["deadline"], items["a"]["constraintType"], items["a"]["constraintDate"]) == \
        ("2026-05-04", "snet", "2026-05-01")
    assert items["m"]["deadline"] == "2026-05-25" and "deadline" not in items["g"]
    assert {i["key"] for i in result["items"]} == {"a", "m"}
    summary = _events(admin, plan_id, "a")[0]
    assert "Ограничение: — → Начало не ранее 01.05.2026" in summary and "Крайний срок: — → 04.05.2026" in summary
    # Ограничения MS Project, кроме «не ранее», хранятся как сведения
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "b", "fields": {"constraintType": "fnlt",
                                                                       "constraintDate": "2026-05-12"}}])
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "c", "fields": {"constraintType": "alap"}}])
    errors = [
        ({"constraintType": "snet"}, "Укажите дату ограничения «Начало не ранее»"),
        ({"constraintDate": "2026-05-01"}, "Укажите тип ограничения"),
        ({"constraintType": "later", "constraintDate": "2026-05-01"}, "Неизвестный тип ограничения"),
        ({"deadline": "31.05.2026"}, "Неверная дата"),
    ]
    for fields, message in errors:
        response = ops(admin, plan_id, [{"op": "item.update", "key": "c", "fields": {"constraintType": None, **fields}}])
        assert response.status_code == 400 and message in response.get_json()["message"], (fields, response.get_json())
    # Снять ограничение — тип и дата вместе
    ok_ops(admin, plan_id, [{"op": "item.update", "key": "a", "fields": {"constraintType": None, "constraintDate": None}}])
    assert _events(admin, plan_id, "a")[0].endswith("Ограничение: Начало не ранее 01.05.2026 → —")


def test_changes_polling_and_snapshot_restore_with_links(app, admin):
    app.config["SNAPSHOT_INTERVAL_MINUTES"] = 0
    _, plan_id = _plan_with_rows(admin)
    base = ok_ops(admin, plan_id, [link("ab", "a", "b", lag=1), link("bc", "b", "c")])["revision"]
    changes = admin.get(f"/api/plans/{plan_id}/changes?since={base - 1}").get_json()
    assert {link["key"] for link in changes["links"]} == {"ab", "bc"}
    snapshot = admin.post(f"/api/plans/{plan_id}/snapshots", json={"label": "Со связями"}).get_json()
    data = admin.get(f"/api/plans/{plan_id}/snapshots/{snapshot['id']}").get_json()["data"]
    assert data["version"] == 2 and {(link["from"], link["to"]) for link in data["links"]} == {("a", "b"), ("b", "c")}
    ok_ops(admin, plan_id, [{"op": "link.delete", "key": "ab"}, {"op": "link.update", "key": "bc", "fields": {"lag": 4}},
                            link("am", "a", "m"), {"op": "item.update", "key": "a", "fields": {"deadline": "2026-06-01"}}])
    result = admin.post(f"/api/plans/{plan_id}/snapshots/{snapshot['id']}/restore").get_json()
    assert result["fixes"] == []
    links = _links(admin, plan_id)
    assert set(links) == {("a", "b"), ("b", "c")} and links[("a", "b")]["key"] == "ab" and links[("b", "c")]["lag"] == 0
    assert next(i for i in _doc(admin, plan_id)["items"] if i["key"] == "a")["deadline"] is None


def test_restoring_snapshot_from_before_links_turns_dependencies_into_links(app, admin):
    """Снимок до 0.7.0: предшественник — поле строки, списка связей нет, поля крайнего срока нет."""
    _, plan_id = _plan_with_rows(admin)
    ok_ops(admin, plan_id, [link("bc", "b", "c", type="SS"), link("am", "a", "m"),
                            {"op": "item.update", "key": "a", "fields": {"deadline": "2026-06-01"}}])
    current = _doc(admin, plan_id)
    old_items = []
    for item in current["items"]:
        item = {k: v for k, v in item.items() if k not in ("updatedAt", "updatedById", "updatedRev", "commentCount",
                                                           "deadline", "constraintType", "constraintDate")}
        if item["type"] == "task":
            item.update({"dependencyKey": None, "dependencyType": "FS", "dependencyLag": 0})
        if item["key"] == "b":
            item.update({"dependencyKey": "a", "dependencyType": "FF", "dependencyLag": 3})
        if item["key"] == "c":
            item.update({"dependencyKey": "b", "dependencyType": "SS", "dependencyLag": 0})
        old_items.append(item)
    data = {"format": "puls-plan", "version": 1, "plan": {"title": current["plan"]["title"]}, "items": old_items,
            "notes": [], "assignees": []}
    with app.app_context():
        snapshot = PlanSnapshot(plan_id=plan_id, revision=1, reason="manual", label="0.6", items_count=len(old_items),
                                data=data)
        db.session.add(snapshot)
        db.session.commit()
        snapshot_id = snapshot.id
    result = admin.post(f"/api/plans/{plan_id}/snapshots/{snapshot_id}/restore").get_json()
    assert result["fixes"] == []
    links = _links(admin, plan_id)
    assert {k: (v["type"], v["lag"], v["lagKind"]) for k, v in links.items()} == \
        {("a", "b"): ("FF", 3, "calendar"), ("b", "c"): ("SS", 0, "calendar")}
    assert links[("b", "c")]["key"] == "bc"  # та же связь осталась, а не пересоздана
    assert next(i for i in _doc(admin, plan_id)["items"] if i["key"] == "a")["deadline"] is None


def test_duplicate_plan_and_json_import_keep_links(app, admin):
    project_id, plan_id = _plan_with_rows(admin)
    ok_ops(admin, plan_id, [link("ab", "a", "b", lag=2, lagKind="working"), link("bm", "b", "m", type="FF"),
                            link("cm", "c", "m")])
    copy = admin.post(f"/api/projects/{project_id}/plans", json={"sourcePlanId": plan_id}).get_json()

    def stable(plan):
        return [{k: v for k, v in link.items() if k != "updatedRev"} for link in _doc(admin, plan)["links"]]
    assert stable(copy["id"]) == stable(plan_id) and len(stable(plan_id)) == 3
    # Импорт снимка до 0.7.0 (JSON «Пульса» версии 1): предшественник строки — связь; цикл и ссылка на группу снимаются
    old = {"format": "puls-plan", "version": 1, "plan": {"title": "Из файла"}, "items": [
        {"key": "x", "type": "task", "name": "X", "start": "2026-05-01", "end": "2026-05-02", "dependencyKey": "y"},
        {"key": "y", "type": "task", "name": "Y", "start": "2026-05-03", "end": "2026-05-04", "dependencyKey": "x"},
        {"key": "z", "type": "task", "name": "Z", "start": "2026-05-03", "end": "2026-05-04", "dependencyKey": "x",
         "dependencyType": "SS", "dependencyLag": 1},
    ]}
    imported = admin.post(f"/api/projects/{project_id}/plans", json={"document": old})
    assert imported.status_code == 201, imported.get_json()
    links = _links(admin, imported.get_json()["id"])
    assert ("x", "z") in links and links[("x", "z")]["type"] == "SS" and len(links) == 2


def test_legacy_json_export_has_first_predecessor_and_reimports_all_links(app, admin):
    project_id, plan_id = _plan_with_rows(admin)
    ok_ops(admin, plan_id, [link("ab", "a", "b", type="SS", lag=2), link("cb", "c", "b", lag=1, lagKind="working"),
                            link("bm", "b", "m"),
                            {"op": "item.update", "key": "c", "fields": {"deadline": "2026-05-20", "constraintType": "snet",
                                                                       "constraintDate": "2026-05-11"}}])
    exported = admin.get(f"/api/plans/{plan_id}/export").get_json()
    rows = {r["id"]: r for r in exported["rows"]}
    # Формат 1.0: один предшественник — первый; все связи — в predecessors (1.0 это поле не читает)
    assert (rows["b"]["dependencyId"], rows["b"]["dependencyType"], rows["b"]["depLag"]) == ("a", "SS", 2)
    assert rows["b"]["predecessors"] == [{"id": "a", "type": "SS", "lag": 2, "lagKind": "calendar"},
                                         {"id": "c", "type": "FS", "lag": 1, "lagKind": "working"}]
    assert rows["c"]["dependencyId"] == "" and "predecessors" not in rows["c"]
    assert rows["m"]["predecessors"] == [{"id": "b", "type": "FS", "lag": 0, "lagKind": "calendar"}]
    again = admin.post(f"/api/projects/{project_id}/plans", json={"document": exported}).get_json()
    links = _links(admin, again["id"])
    assert {k: (v["type"], v["lag"], v["lagKind"]) for k, v in links.items()} == {
        ("a", "b"): ("SS", 2, "calendar"), ("c", "b"): ("FS", 1, "working"), ("b", "m"): ("FS", 0, "calendar")}
    # Крайний срок и ограничение — тоже дополнительными полями строки
    assert (rows["c"]["deadline"], rows["c"]["constraintType"], rows["c"]["constraintDate"]) == \
        ("2026-05-20", "snet", "2026-05-11")
    c = next(i for i in _doc(admin, again["id"])["items"] if i["key"] == "c")
    assert (c["deadline"], c["constraintType"], c["constraintDate"]) == ("2026-05-20", "snet", "2026-05-11")
    # Файл 1.0.x: dependencyId задачи — связь
    state = {"rows": [
        {"id": "r1", "type": "task", "name": "Первая", "start": "2026-05-01", "end": "2026-05-02"},
        {"id": "r2", "type": "task", "name": "Вторая", "start": "2026-05-03", "end": "2026-05-04", "dependencyId": "r1",
         "dependencyType": "FF", "depLag": 1},
        {"id": "r3", "type": "task", "name": "Третья", "start": "2026-05-03", "end": "2026-05-04", "dependencyId": "r9"},
    ]}
    legacy = admin.post(f"/api/projects/{project_id}/plans", json={"document": state}).get_json()
    assert {k: (v["type"], v["lag"]) for k, v in _links(admin, legacy["id"]).items()} == {("r1", "r2"): ("FF", 1)}


def test_integration_api_items_list_predecessors(app, admin, client):
    project_id, plan_id = _plan_with_rows(admin)
    ok_ops(admin, plan_id, [link("ab", "a", "b", type="SS", lag=2), link("cm", "c", "m"), link("bm", "b", "m"),
                            {"op": "item.update", "key": "m", "fields": {"deadline": "2026-05-30"}}])
    key = admin.post("/api/settings/integration-keys", json={"name": "Дашборды", "app": "dashboards",
                                                             "scopes": ["projects.read"]}).get_json()["key"]
    res = client.get(f"/api/integration/v1/projects/{project_id}/items", headers={"Authorization": f"Bearer {key}"})
    assert res.status_code == 200
    items = {i["key"]: i for i in res.get_json()["items"]}
    # Прежние поля не изменились, новые — добавлены
    assert {"key", "name", "type", "start", "end", "status", "progress", "group", "path"} <= set(items["b"])
    assert items["b"]["predecessors"] == [{"key": "a", "type": "SS", "lag": 2, "lagKind": "calendar"}]
    assert [p["key"] for p in items["m"]["predecessors"]] == ["c", "b"] and items["m"]["deadline"] == "2026-05-30"
    assert items["a"]["predecessors"] == [] and items["a"]["deadline"] is None


# ---------- миграция 0007: предшественник строки → связь ----------

def _alembic(url):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = url
    return cfg


def test_migration_moves_dependencies_to_links_and_back(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", url)
    cfg = _alembic(url)
    cwd = os.getcwd()
    os.chdir(BACKEND)
    try:
        command.upgrade(cfg, "0006_integration_keys")
        engine = create_engine(url)
        now = "2026-10-30 10:00:00"
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO projects (id, title, subtitle, position, created_at, updated_at) "
                              "VALUES (1, 'П', '', 1, :now, :now)"), {"now": now})
            conn.execute(text("INSERT INTO plans (id, project_id, key, title, subtitle, position, settings, revision, "
                              "meta_rev, next_row_seq, is_archived, created_at, updated_at) VALUES (1, 1, 'p1', 'План', '', "
                              "1, '{}', 5, 0, 1, 0, :now, :now)"), {"now": now})
            rows = [
                # key, type, dependency_key, dependency_type, dependency_lag, deleted_at
                ("a", "task", None, "SF", 0, None),            # тип без предшественника — не связь
                ("b", "task", "a", "SS", 2, None),
                ("c", "task", "b", None, None, None),          # без типа — FS
                ("d", "task", "a", "FF", -1, "2026-10-29 09:00:00"),  # удалённая строка — удалённая связь
                ("e", "task", "missing", "FS", 0, None),       # ссылка на отсутствующую строку не переносится
                ("g", "group", None, None, None, None),
                ("f", "task", "g", "FS", 0, None),             # группа не может быть предшественником
                ("h", "task", "h", "FS", 0, None),             # сама на себя
            ]
            for i, (key, kind, dep, dep_type, lag, deleted) in enumerate(rows, start=1):
                conn.execute(text(
                    "INSERT INTO plan_items (id, plan_id, key, type, name, position, start_date, end_date, dependency_key, "
                    "dependency_type, dependency_lag, comment_count, created_at, updated_at, updated_rev, deleted_at) "
                    "VALUES (:id, 1, :key, :type, :key, :id, '2026-05-01', '2026-05-02', :dep, :dt, :lag, 0, :now, :now, "
                    ":id, :deleted)"),
                    {"id": i, "key": key, "type": kind, "dep": dep, "dt": dep_type, "lag": lag, "now": now,
                     "deleted": deleted})
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            links = conn.execute(text("SELECT key, from_key, to_key, type, lag, lag_kind, deleted_at, updated_rev "
                                      "FROM plan_links ORDER BY id")).all()
        assert [tuple(r[:6]) for r in links] == [("lm2", "a", "b", "SS", 2, "calendar"), ("lm3", "b", "c", "FS", 0, "calendar"),
                                                 ("lm4", "a", "d", "FF", -1, "calendar")]
        assert [r[6] is not None for r in links] == [False, False, True] and links[0][7] == 2

        # 0.7.0 работает с перенесёнными связями: вторая связь в веху и правка через приложение
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO plan_links (plan_id, key, from_key, to_key, type, lag, lag_kind, created_at, "
                              "updated_at, updated_rev) VALUES (1, 'l2', 'c', 'b', 'FS', 0, 'working', :now, :now, 6)"),
                         {"now": now})
            conn.execute(text("UPDATE plan_links SET deleted_at = :now WHERE key = 'lm3'"), {"now": now})

        # Откат: первая живая связь задачи — в столбцы 0.6; у удалённой строки — её связь; снятые связи — сняты
        command.downgrade(cfg, "0006_integration_keys")
        with engine.begin() as conn:
            deps = {r[0]: tuple(r[1:]) for r in conn.execute(text(
                "SELECT key, dependency_key, dependency_type, dependency_lag FROM plan_items")).all()}
            tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).all()}
            columns = {r[1] for r in conn.execute(text("PRAGMA table_info(plan_items)")).all()}
        assert deps["b"] == ("a", "SS", 2) and deps["d"] == ("a", "FF", -1)
        assert deps["c"][0] is None and deps["a"][0] is None
        # Строки без связей — как были до обновления (и недействительная ссылка тоже: её снимет проверка 0.6)
        assert deps["e"] == ("missing", "FS", 0)
        assert "plan_links" not in tables and "deadline" not in columns

        # И снова вверх: данные на месте
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            again = conn.execute(text("SELECT from_key, to_key, deleted_at IS NULL FROM plan_links ORDER BY id")).all()
        assert [tuple(r) for r in again] == [("a", "b", 1), ("a", "d", 0)]
        engine.dispose()
    finally:
        os.chdir(cwd)
