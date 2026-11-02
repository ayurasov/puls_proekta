"""Ревью 0.7.0 (связи): восстановление версии большого плана, связи только из версии, проверка циклов один раз на
пакет, возврат связей при восстановлении многих строк, идентификаторы MS Project только из серверных пакетов,
связь строки с родительской или вложенной."""

import builtins
import io
import json

from sqlalchemy import select

from app.extensions import db
from app.models import PlanSnapshot
from app.services import plan_ops
from tests.helpers import create_project, ok_ops, ops, task

NS = "http://schemas.microsoft.com/project"


def _doc(client, plan_id):
    return client.get(f"/api/plans/{plan_id}").get_json()


def _links(client, plan_id):
    return sorted((link["from"], link["to"]) for link in _doc(client, plan_id)["links"])


def test_review_restore_snapshot_of_large_plan_is_not_capped(app, admin):
    """После замены 1 200 строк на 900 версия «до» восстанавливается: 900 удалений и 1 200 возвратов — больше 2 000
    операций, но пакет собирает сервер."""
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task(f"t{n}", f"T{n}") for n in range(1200)])
    snapshot = admin.post(f"/api/plans/{plan_id}/snapshots", json={"label": "До замены"}).get_json()
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": f"t{n}"} for n in range(1200)])
    ok_ops(admin, plan_id, [task(f"n{n}", f"N{n}") for n in range(900)])
    res = admin.post(f"/api/plans/{plan_id}/snapshots/{snapshot['id']}/restore")
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["changes"] == 2100
    names = {i["name"] for i in _doc(admin, plan_id)["items"]}
    assert len(names) == 1200 and "T0" in names and "N0" not in names


def test_review_snapshot_restore_takes_links_only_from_snapshot(app, admin):
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("a", "A"), task("b", "B")])
    snapshot = admin.post(f"/api/plans/{plan_id}/snapshots", json={"label": "Без связей"}).get_json()
    ok_ops(admin, plan_id, [{"op": "link.create", "link": {"key": "lx", "from": "a", "to": "b"}}])
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "a"}])  # связь удалена вместе со строкой
    res = admin.post(f"/api/plans/{plan_id}/snapshots/{snapshot['id']}/restore")
    assert res.status_code == 200, res.get_json()
    assert _links(admin, plan_id) == []
    # Обычное восстановление строки (отмена удаления) по-прежнему возвращает её связи
    ok_ops(admin, plan_id, [{"op": "link.create", "link": {"key": "ly", "from": "a", "to": "b"}}])
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "a"}])
    ok_ops(admin, plan_id, [{"op": "item.restore", "key": "a"}])
    assert _links(admin, plan_id) == [("a", "b")]


def test_review_cycles_are_checked_once_per_batch(app, admin, monkeypatch):
    """Цепочка, записанная снизу вверх, не обходится заново для каждой связи: без цикла граф проверяется одним
    проходом (раньше — обход графа на каждую связь, 20 000 строк — минуты)."""
    calls = []
    original = plan_ops.PlanEditor._reaches
    monkeypatch.setattr(plan_ops.PlanEditor, "_reaches", lambda self, a, b: calls.append(1) or original(self, a, b))
    project_id, _ = create_project(admin)
    n = 400
    items = [{"key": f"t{k}", "type": "task", "name": f"Работа {k}", "start": "2026-05-01", "end": "2026-05-02",
              "position": float(k)} for k in range(n)]
    links = [{"key": f"l{k}", "from": f"t{k + 1}", "to": f"t{k}"} for k in reversed(range(n - 1))]
    res = admin.post(f"/api/projects/{project_id}/plans",
                     json={"document": {"format": "puls-plan", "version": 2, "items": items, "links": links}})
    assert res.status_code == 201, res.get_json()
    assert len(_doc(admin, res.get_json()["id"])["links"]) == n - 1
    assert calls == []
    # Правка сотрудника, замыкающая цикл, по-прежнему отклоняется с сообщением о своей связи
    plan_id = res.get_json()["id"]
    response = ops(admin, plan_id, [{"op": "link.create", "link": {"key": "lz", "from": "t0", "to": f"t{n - 1}"}}])
    assert response.status_code == 400 and response.get_json()["error"] == "link_cycle"
    assert response.get_json()["message"].startswith("Нельзя связать «Работа 0» → «Работа 399»")
    # Из файла связь, замыкающая цикл, пропускается, остальные создаются
    cyclic = links + [{"key": "lc", "from": "t0", "to": f"t{n - 1}"}]
    res = admin.post(f"/api/projects/{project_id}/plans",
                     json={"document": {"format": "puls-plan", "version": 2, "items": items, "links": cyclic}})
    assert res.status_code == 201, res.get_json()
    keys = {link["key"] for link in _doc(admin, res.get_json()["id"])["links"]}
    assert len(keys) == n - 1 and "lc" not in keys


def test_review_restoring_many_rows_indexes_links_once(app, admin, monkeypatch):
    """Возврат связей восстановленных строк — один проход по связям пакета, а не сортировка всех связей на каждую
    строку (2 000 строк и 4 000 связей восстанавливались 9 с)."""
    _, plan_id = create_project(admin)
    n = 300
    ok_ops(admin, plan_id, [task(f"t{k}", f"T{k}") for k in range(n)])
    ok_ops(admin, plan_id, [{"op": "link.create", "link": {"key": f"l{k}", "from": f"t{k}", "to": f"t{k + 1}"}}
                            for k in range(n - 1)])
    half = n // 2
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": f"t{k}"} for k in range(half)])
    assert len(_doc(admin, plan_id)["links"]) == n - 1 - half
    sorts = []
    monkeypatch.setattr(plan_ops, "sorted", lambda *args, **kwargs: sorts.append(1) or builtins.sorted(*args, **kwargs),
                        raising=False)
    result = ok_ops(admin, plan_id, [{"op": "item.restore", "key": f"t{k}"} for k in range(half)])
    assert len(sorts) <= 1
    assert len(result["links"]) == half and len(_doc(admin, plan_id)["links"]) == n - 1


def test_review_link_between_parent_and_subtask_is_rejected(app, admin):
    _, plan_id = create_project(admin)
    ok_ops(admin, plan_id, [task("p", "Родительская", end="2026-05-20"), task("c", "Вложенная", parentKey="p"),
                            task("g", "Внучка", parentKey="c"), task("x", "Другая")])
    for frm, to in (("p", "c"), ("c", "p"), ("p", "g"), ("g", "p")):
        response = ops(admin, plan_id, [{"op": "link.create", "link": {"key": "l1", "from": frm, "to": to}}])
        assert response.status_code == 400 and "вложена" in response.get_json()["message"], (frm, to)
    ok_ops(admin, plan_id, [{"op": "link.create", "link": {"key": "l1", "from": "x", "to": "c"}}])
    response = ops(admin, plan_id, [{"op": "link.update", "key": "l1", "fields": {"from": "p"}}])
    assert response.status_code == 400 and "вложена" in response.get_json()["message"]


# ---------- идентификаторы строк в MS Project ----------

def _mspdi(tasks):
    parts = [f'<?xml version="1.0" encoding="UTF-8"?><Project xmlns="{NS}"><Name>t.xml</Name>'
             "<GUID>11111111-2222-3333-4444-555555555555</GUID><Tasks><Task><UID>0</UID><ID>0</ID><Name>P</Name>"
             "<OutlineLevel>0</OutlineLevel><Summary>1</Summary></Task>"]
    for n, (uid, name) in enumerate(tasks, start=1):
        parts.append(f"<Task><UID>{uid}</UID><GUID>00000000-0000-4000-8000-{uid:012d}</GUID><ID>{n}</ID>"
                     f"<Name>{name}</Name><OutlineLevel>1</OutlineLevel><OutlineNumber>{n}</OutlineNumber>"
                     "<Start>2026-03-02T08:00:00</Start><Finish>2026-03-02T17:00:00</Finish>"
                     "<Duration>PT8H0M0S</Duration></Task>")
    parts.append("</Tasks></Project>")
    return "".join(parts).encode()


def _form(data, target, options, **fields):
    form = {"file": (io.BytesIO(data), "t.xml"), "target": target, "options": json.dumps(options)}
    form.update({k: str(v) for k, v in fields.items()})
    return form


def _import(client, data, target, options, **fields):
    res = client.post("/api/import/msproject/preview", data=_form(data, target, options, **fields),
                      content_type="multipart/form-data")
    assert res.status_code == 200, res.get_json()
    preview = res.get_json()
    res = client.post("/api/import/msproject/apply", content_type="multipart/form-data",
                      data=_form(data, target, options, fingerprint=preview["fingerprint"], **fields))
    assert res.status_code == 200, res.get_json()
    return preview, res.get_json()


def test_review_browser_copy_of_row_does_not_take_ms_project_ids(app, admin):
    project_id, _ = create_project(admin)
    data = _mspdi([(1, "A"), (2, "B")])
    _, result = _import(admin, data, "plan", {"mode": "master"}, projectId=project_id)
    plan_id = result["planId"]
    a = next(i for i in _doc(admin, plan_id)["items"] if i["name"] == "A")
    assert a["extra"]["msp"]["uid"] == 1
    # Так копию строки отправлял браузер до исправления: вместе с extra.msp
    copy = {k: v for k, v in a.items() if k not in ("updatedAt", "updatedById", "updatedRev", "commentCount")}
    copy.update({"key": "icopy", "name": "A (копия)", "position": a["position"] + 1,
                 "extra": {**a["extra"], "legacyNote": "служебное"}})
    ok_ops(admin, plan_id, [{"op": "item.create", "item": copy},
                            {"op": "item.update", "key": "i2", "fields": {"extra": {"msp": {"uid": 99}}}}])
    items = {i["key"]: i for i in _doc(admin, plan_id)["items"]}
    assert items["icopy"]["extra"] == {"legacyNote": "служебное"}
    assert items["i2"]["extra"]["msp"]["uid"] == 2
    # Копия — строка «Пульса»: обновление из файла её не трогает
    preview, _ = _import(admin, data, "update", {"mode": "master", "deleteMissing": True}, planId=plan_id)
    assert preview["changes"]["missing"] == []
    assert sorted(i["name"] for i in _doc(admin, plan_id)["items"]) == ["A", "A (копия)", "B"]
    # Восстановление версии (пакет сервера) идентификаторы сохраняет
    with app.app_context():
        snapshot_id = db.session.execute(select(PlanSnapshot.id).where(PlanSnapshot.plan_id == plan_id)
                                         .order_by(PlanSnapshot.id.desc())).scalars().first()
    ok_ops(admin, plan_id, [{"op": "item.delete", "key": "i1"}])
    assert admin.post(f"/api/plans/{plan_id}/snapshots/{snapshot_id}/restore").status_code == 200
    assert next(i for i in _doc(admin, plan_id)["items"] if i["key"] == "i1")["extra"]["msp"]["uid"] == 1
