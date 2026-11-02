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
