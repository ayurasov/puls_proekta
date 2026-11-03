"""Импорт MS Project через API (ADR-0012): новый проект и план, замена, обновление мастер-копии, выгрузка."""

import io
import json
from pathlib import Path

from sqlalchemy import select

from app.extensions import db
from app.models import ItemComment, PlanSnapshot
from app.services.msproject import read_mspdi
from tests.conftest import new_client
from tests.helpers import create_project, ok_ops
from tests.test_msproject import big_file

CRM = Path(__file__).parent / "fixtures" / "msproject" / "vnedrenie_crm.xml"


def _form(data, target="project", options=None, **fields):
    form = {"file": (io.BytesIO(data), "Внедрение CRM.xml"), "target": target, "options": json.dumps(options or {})}
    form.update({k: str(v) for k, v in fields.items() if v is not None})
    return form


def _preview(client, data, target="project", options=None, **fields):
    return client.post("/api/import/msproject/preview", data=_form(data, target, options, **fields),
                       content_type="multipart/form-data")


def _apply(client, data, target="project", options=None, fingerprint=None, **fields):
    if fingerprint is None:
        res = _preview(client, data, target, options, **fields)
        assert res.status_code == 200, res.get_json()
        fingerprint = res.get_json()["fingerprint"]
        fields.setdefault("planRevision", res.get_json().get("planRevision"))
    return client.post("/api/import/msproject/apply", data=_form(data, target, options, fingerprint=fingerprint, **fields),
                       content_type="multipart/form-data")


def _plan(client, plan_id):
    return client.get(f"/api/plans/{plan_id}").get_json()


def test_preview_shows_plan_and_writes_nothing(app, admin):
    data = CRM.read_bytes()
    before = admin.get("/api/projects").get_json()
    res = _preview(admin, data)
    body = res.get_json()
    assert res.status_code == 200, body
    assert body["stats"]["tasks"] > 10 and body["stats"]["links"] > 5 and body["tree"][0]["depth"] == 0
    assert body["fingerprint"] and any(w["code"] for w in body["warnings"])
    assert body["customFields"], "настраиваемые поля для выбора категории"
    assert admin.get("/api/projects").get_json() == before


def test_new_project_once(app, admin):
    data = CRM.read_bytes()
    res = _apply(admin, data, options={"mode": "once"})
    assert res.status_code == 200, res.get_json()
    doc = _plan(admin, res.get_json()["planId"])
    items = doc["items"]
    preview = _preview(admin, data).get_json()
    assert len(items) == preview["stats"]["tasks"] + preview["stats"]["groups"] + preview["stats"]["milestones"]
    assert len(doc["links"]) == preview["stats"]["links"]
    assert doc["plan"]["settings"]["source"]["kind"] == "msproject"
    assert doc["plan"]["settings"]["source"]["master"] is False
    assert all(i["extra"]["msp"]["uid"] for i in items)
    with app.app_context():
        notes = db.session.execute(select(ItemComment).where(ItemComment.plan_id == doc["plan"]["id"])).scalars().all()
        assert notes and all(n.text.startswith("Заметка из MS Project") for n in notes)
    project = admin.get(f"/api/projects/{res.get_json()['projectId']}").get_json()
    assert project["title"]


def test_rights_and_outdated_preview(app, admin):
    data = CRM.read_bytes()
    editor = new_client(app, "redaktor", "editor")
    assert _preview(editor, data).status_code == 403
    project_id, _plan_id = create_project(admin)
    assert _preview(editor, data, "plan", projectId=project_id).status_code == 404
    res = _apply(admin, data, fingerprint="0" * 64)
    assert res.status_code == 409 and res.get_json()["error"] == "preview_outdated"
    mpp = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600
    res = _preview(admin, mpp)
    assert res.status_code == 400 and "XML" in res.get_json()["message"]


def test_new_plan_master_and_update_from_file(app, admin):
    project_id, _ = create_project(admin)
    data = CRM.read_bytes()
    res = _apply(admin, data, "plan", {"mode": "master"}, projectId=project_id)
    assert res.status_code == 200, res.get_json()
    plan_id = res.get_json()["planId"]
    doc = _plan(admin, plan_id)
    assert doc["plan"]["settings"]["source"]["master"] is True
    task = next(i for i in doc["items"] if i["type"] == "task" and i["name"] == "Разработка устава проекта")
    # В «Пульсе» добавили комментарий и цвет — их обновление не трогает
    ok_ops(admin, plan_id, [{"op": "item.update", "key": task["key"], "fields": {"color": "red"}}])
    admin.post(f"/api/plans/{plan_id}/items/{task['key']}/comments", json={"text": "Обсудили с заказчиком"})

    text = data.decode("utf-8").replace("<Name>Разработка устава проекта</Name>", "<Name>Устав проекта</Name>")
    changed = text.encode("utf-8")
    preview = _preview(admin, changed, "update", {"mode": "master"}, planId=plan_id).get_json()
    assert preview["changes"]["changed"] and preview["planRevision"]
    renamed = next(c for c in preview["changes"]["changed"] if c["key"] == task["key"])
    assert renamed["fields"]["name"] == {"before": "Разработка устава проекта", "after": "Устав проекта"}

    # Пока проверяли, план изменился — применить нельзя
    stale = preview["planRevision"]
    ok_ops(admin, plan_id, [{"op": "item.update", "key": task["key"], "fields": {"barText": "x"}}])
    res = _apply(admin, changed, "update", {"mode": "master"}, fingerprint=preview["fingerprint"], planId=plan_id,
                 planRevision=stale)
    assert res.status_code == 409 and res.get_json()["error"] == "plan_changed"

    res = _apply(admin, changed, "update", {"mode": "master"}, planId=plan_id)
    assert res.status_code == 200, res.get_json()
    after = {i["key"]: i for i in _plan(admin, plan_id)["items"]}
    assert after[task["key"]]["name"] == "Устав проекта" and after[task["key"]]["color"] == "red"
    with app.app_context():
        assert db.session.execute(select(ItemComment).where(
            ItemComment.plan_id == plan_id, ItemComment.text == "Обсудили с заказчиком")).scalar_one()
        assert db.session.execute(select(PlanSnapshot).where(
            PlanSnapshot.plan_id == plan_id, PlanSnapshot.reason == "before_import")).scalars().first()
    # Повторное обновление тем же файлом — ничего не меняет
    again = _preview(admin, changed, "update", {"mode": "master"}, planId=plan_id).get_json()
    assert not again["changes"]["changed"] and not again["changes"]["new"]


def test_replace_plan_content(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [{"op": "item.create", "item": {"key": "old", "type": "task", "name": "Прежняя задача",
                                                            "start": "2026-05-01", "end": "2026-05-05"}}])
    data = CRM.read_bytes()
    preview = _preview(admin, data, "replace", planId=plan_id).get_json()
    assert preview["replacing"] == 1
    res = _apply(admin, data, "replace", planId=plan_id)
    assert res.status_code == 200, res.get_json()
    names = {i["name"] for i in _plan(admin, plan_id)["items"]}
    assert "Прежняя задача" not in names and "Кик-офф встреча с заказчиком" in names
    with app.app_context():
        snap = db.session.execute(select(PlanSnapshot).where(
            PlanSnapshot.plan_id == plan_id, PlanSnapshot.reason == "before_import")).scalars().first()
        assert any(i["name"] == "Прежняя задача" for i in snap.data["items"])


def test_large_file_is_not_limited_by_batch_size(app, admin):
    data = big_file(groups=60, per_group=40)   # 2460 строк и 2340 связей — больше 2000 операций
    res = _apply(admin, data)
    assert res.status_code == 200, res.get_json()
    doc = _plan(admin, res.get_json()["planId"])
    assert len(doc["items"]) == 2460 and len(doc["links"]) == 60 * 39


def test_export_round_trip(app, admin):
    res = _apply(admin, CRM.read_bytes())
    plan_id = res.get_json()["planId"]
    doc = _plan(admin, plan_id)
    exported = admin.get(f"/api/plans/{plan_id}/export/msproject")
    assert exported.status_code == 200 and exported.mimetype == "application/xml"
    assert "attachment" in exported.headers["Content-Disposition"]
    project = read_mspdi(exported.data)
    rows = [t for t in project.tasks if t.uid != 0]
    assert len(rows) == len(doc["items"])
    assert sum(len(t.links) for t in project.tasks) == len(doc["links"]) if hasattr(project.tasks[0], "links") else True
    outsider = new_client(app, "chuzhoy", "editor")
    assert outsider.get(f"/api/plans/{plan_id}/export/msproject").status_code == 404


# ---------- ревью 0.7.0 ----------

def _master_plan(admin):
    project_id, _ = create_project(admin)
    res = _apply(admin, CRM.read_bytes(), "plan", {"mode": "master", "categoryField": "Этап"}, projectId=project_id)
    assert res.status_code == 200, res.get_json()
    return project_id, res.get_json()["planId"]


def test_review_archived_plan_is_not_replaced_or_updated(app, admin):
    project_id, plan_id = _master_plan(admin)
    other = admin.post(f"/api/projects/{project_id}/plans", json={"title": "Ещё план"}).get_json()
    assert other["id"]
    assert admin.post(f"/api/plans/{plan_id}/archive").status_code == 200
    for target in ("update", "replace"):
        res = _preview(admin, CRM.read_bytes(), target, {"mode": "master"}, planId=plan_id)
        assert res.status_code == 409 and res.get_json()["error"] == "archived", target


def test_review_update_writes_row_history_keeps_master_and_adds_categories(app, admin):
    from app.models import PlanEvent

    _project_id, plan_id = _master_plan(admin)
    task = next(i for i in _plan(admin, plan_id)["items"] if i["name"] == "Разработка устава проекта")
    changed = CRM.read_bytes().decode().replace("<Name>Разработка устава проекта</Name>", "<Name>Устав проекта</Name>")
    # Обновление без явного режима — план остаётся мастер-копией
    res = _apply(admin, changed.encode(), "update", {"categoryField": "Этап"}, planId=plan_id)
    assert res.status_code == 200, res.get_json()
    plan = _plan(admin, plan_id)["plan"]
    assert plan["settings"]["source"]["master"] is True
    with app.app_context():
        events = db.session.execute(select(PlanEvent).where(
            PlanEvent.plan_id == plan_id, PlanEvent.entity_key == task["key"])).scalars().all()
        assert any("Устав проекта" in (e.summary or "") for e in events), [e.summary for e in events]


def test_review_resources_matched_by_email_only_for_admin(app, admin):
    from tests.conftest import make_user

    make_user(app, "drugoy", "editor", name="Совсем Другой Человек", email="ivanov@romashka.ru")
    project_id, _ = create_project(admin)
    by_admin = _preview(admin, CRM.read_bytes(), "plan", projectId=project_id).get_json()
    assert any(a["userName"] == "Совсем Другой Человек" for a in by_admin["assignees"])
    editor = new_client(app, "pravit", "editor")
    from tests.test_projects_access import _uid
    admin.post("/api/access", json={"userIds": [_uid(app, "pravit")], "projectIds": [project_id], "level": "edit"})
    by_editor = _preview(editor, CRM.read_bytes(), "plan", projectId=project_id).get_json()
    assert not any(a["userName"] == "Совсем Другой Человек" for a in by_editor["assignees"])


def test_review_restoring_before_import_snapshot_clears_master_source(app, admin):
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [{"op": "item.create", "item": {"key": "old", "type": "task", "name": "Прежняя",
                                                            "start": "2026-05-01", "end": "2026-05-05"}}])
    assert _apply(admin, CRM.read_bytes(), "replace", {"mode": "master"}, planId=plan_id).status_code == 200
    assert _plan(admin, plan_id)["plan"]["settings"]["source"]["master"] is True
    snaps = admin.get(f"/api/plans/{plan_id}/snapshots").get_json()
    before = next(s for s in snaps if s["reason"] == "before_import")
    assert admin.post(f"/api/plans/{plan_id}/snapshots/{before['id']}/restore").status_code == 200
    restored = _plan(admin, plan_id)
    assert "source" not in restored["plan"]["settings"]
    assert {i["name"] for i in restored["items"]} == {"Прежняя"}


def test_review_update_keeps_one_time_mode_unless_chosen(app, admin):
    project_id, _ = create_project(admin)
    res = _apply(admin, CRM.read_bytes(), "plan", {"mode": "once"}, projectId=project_id)
    plan_id = res.get_json()["planId"]
    assert _apply(admin, CRM.read_bytes(), "update", {}, planId=plan_id).status_code == 200
    assert _plan(admin, plan_id)["plan"]["settings"]["source"]["master"] is False
    assert _apply(admin, CRM.read_bytes(), "update", {"mode": "master"}, planId=plan_id).status_code == 200
    assert _plan(admin, plan_id)["plan"]["settings"]["source"]["master"] is True


def _small_file(resource):
    """Одна задача на ресурсе resource; заметка в RTF с байтом \\'00, который в базу попасть не должен."""
    ns = "http://schemas.microsoft.com/project"
    return (f'<?xml version="1.0" encoding="UTF-8"?><Project xmlns="{ns}"><Name>t.xml</Name>'
            "<Tasks><Task><UID>0</UID><ID>0</ID><Name>P</Name><OutlineLevel>0</OutlineLevel><Summary>1</Summary></Task>"
            "<Task><UID>1</UID><ID>1</ID><Name>A</Name><OutlineLevel>1</OutlineLevel><Start>2026-03-02T08:00:00</Start>"
            "<Finish>2026-03-02T17:00:00</Finish><Duration>PT8H0M0S</Duration><Notes>{\\rtf1 a\\'00b}</Notes></Task></Tasks>"
            f"<Resources><Resource><UID>1</UID><ID>1</ID><Name>{resource}</Name><Type>1</Type></Resource></Resources>"
            "<Assignments><Assignment><UID>1</UID><TaskUID>1</TaskUID><ResourceUID>1</ResourceUID></Assignment></Assignments>"
            "</Project>").encode()


def test_review_replace_with_new_resource_takes_free_key(app, admin):
    project_id, _ = create_project(admin)
    res = _apply(admin, _small_file("Иванов Иван"), "plan", {"mode": "once"}, projectId=project_id)
    plan_id = res.get_json()["planId"]
    with app.app_context():
        note = db.session.execute(select(ItemComment).where(ItemComment.plan_id == plan_id)).scalar_one()
        assert note.text.endswith("ab") and "\x00" not in note.text
    # Другой пользователь заменяет план файлом, где у задачи другой ресурс: ключ прежнего исполнителя занят
    other = new_client(app, "pm2", "admin")
    assert _apply(other, _small_file("Петров Пётр"), "replace", {"mode": "once"}, planId=plan_id).status_code == 200
    doc = _plan(admin, plan_id)
    by_key = {a["key"]: a["name"] for a in doc["assignees"]}
    assert sorted(by_key.values()) == ["Иванов Иван", "Петров Пётр"]
    assert [by_key[i["assigneeKey"]] for i in doc["items"]] == ["Петров Пётр"]
    # Тот же пользователь — прежний исполнитель не переименовывается
    assert _apply(admin, _small_file("Сидоров Семён"), "replace", {"mode": "once"}, planId=plan_id).status_code == 200
    names = sorted(a["name"] for a in _plan(admin, plan_id)["assignees"])
    assert names == ["Иванов Иван", "Петров Пётр", "Сидоров Семён"]


def test_after_import_version_for_every_target(app, admin):
    """ADR-0012, раздел 2: после импорта — версия «После импорта»; перед заменой и обновлением — «Перед импортом»."""
    def reasons(plan_id):
        return [s["reason"] for s in admin.get(f"/api/plans/{plan_id}/snapshots").get_json()]

    res = _apply(admin, CRM.read_bytes(), "project", {"mode": "master"})
    plan_id = res.get_json()["planId"]
    assert reasons(plan_id) == ["after_import"]
    assert _apply(admin, CRM.read_bytes(), "update", {}, planId=plan_id).status_code == 200
    assert sorted(reasons(plan_id)) == ["after_import", "after_import", "before_import"]
    snaps = admin.get(f"/api/plans/{plan_id}/snapshots").get_json()
    after = next(s for s in snaps if s["reason"] == "after_import")
    assert after["label"] == "Импорт из MS Project: Внедрение CRM.xml"
    assert after["itemsCount"] == len(_plan(admin, plan_id)["items"])


def test_export_emails_only_for_admin(app, admin):
    """Email сотрудника — в выгрузке MS Project только у администратора: наблюдатель адресов не видит."""
    from tests.conftest import make_user
    from tests.test_projects_access import _uid

    make_user(app, "petrov", "editor", name="Петров Пётр", email="petrov@romashka.ru")
    project_id, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [
        {"op": "assignee.create", "assignee": {"key": "a1", "name": "Петров Пётр", "userId": _uid(app, "petrov")}},
        {"op": "item.create", "item": {"key": "t1", "type": "task", "name": "Задача", "start": "2026-05-04",
                                       "end": "2026-05-08", "assigneeKey": "a1"}}])
    assert b"petrov@romashka.ru" in admin.get(f"/api/plans/{plan_id}/export/msproject").data
    viewer = new_client(app, "smotrit", "viewer")
    admin.post("/api/access", json={"userIds": [_uid(app, "smotrit")], "projectIds": [project_id], "level": "view"})
    response = viewer.get(f"/api/plans/{plan_id}/export/msproject")
    assert response.status_code == 200
    assert b"petrov@romashka.ru" not in response.data and "Петров Пётр".encode() in response.data


def test_update_renamed_resource_renames_assignee(app, admin):
    """Ресурс переименовали в MS Project: при обновлении — тот же исполнитель с новым ФИО, а не второй."""
    project_id, _ = create_project(admin)
    plan_id = _apply(admin, _small_file("Сидоров Алексей"), "plan", {"mode": "master"},
                     projectId=project_id).get_json()["planId"]
    before = _plan(admin, plan_id)["assignees"]
    preview = _preview(admin, _small_file("Сидоров Алексей Петрович"), "update", {}, planId=plan_id).get_json()
    assert preview["changes"]["assignees"]["renamed"] == [
        {"key": before[0]["key"], "before": "Сидоров Алексей", "after": "Сидоров Алексей Петрович"}]
    assert _apply(admin, _small_file("Сидоров Алексей Петрович"), "update", {}, planId=plan_id).status_code == 200
    doc = _plan(admin, plan_id)
    assert [(a["key"], a["name"]) for a in doc["assignees"]] == [(before[0]["key"], "Сидоров Алексей Петрович")]
    assert doc["items"][0]["assigneeKey"] == before[0]["key"]


def _no_guid_file(names):
    """Файл без GUID проекта и задач (MS Project 2003 и старше): задачи с UID 1, 2, 3…"""
    ns = "http://schemas.microsoft.com/project"
    tasks = "".join(f"<Task><UID>{n}</UID><ID>{n}</ID><Name>{name}</Name><OutlineLevel>1</OutlineLevel>"
                    "<Start>2026-03-02T08:00:00</Start><Finish>2026-03-02T17:00:00</Finish><Duration>PT8H0M0S</Duration>"
                    "</Task>" for n, name in enumerate(names, start=1))
    return (f'<?xml version="1.0" encoding="UTF-8"?><Project xmlns="{ns}"><Name>old.xml</Name><Tasks>'
            "<Task><UID>0</UID><ID>0</ID><Name>P</Name><OutlineLevel>0</OutlineLevel><Summary>1</Summary></Task>"
            f"{tasks}</Tasks></Project>").encode()


def test_update_from_other_project_without_guids_warns(app, admin):
    """Без GUID строки сопоставляются по UID: файл другого проекта (другие названия у большинства) — предупреждение;
    предупреждения плана обновления видны в общем списке окна загрузки."""
    project_id, _ = create_project(admin)
    names = ["Анализ", "Проектирование", "Разработка", "Тестирование"]
    plan_id = _apply(admin, _no_guid_file(names), "plan", {"mode": "master"}, projectId=project_id).get_json()["planId"]
    same = _preview(admin, _no_guid_file(["Анализ рынка", *names[1:]]), "update", {}, planId=plan_id).get_json()
    assert "uid_names_differ" not in [w["code"] for w in same["warnings"]]
    other = _preview(admin, _no_guid_file(["Task 1", "Second Task", "Third", "Разработка"]), "update", {},
                     planId=plan_id).get_json()
    warning = other["warnings"][0]
    assert warning["code"] == "uid_names_differ" and warning["count"] == 4 and "другого проекта" in warning["message"]
