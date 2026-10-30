"""Перенос реальной базы 1.0.1 (снимок legacy/snapshot) и сверка без потерь."""

import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    AccessRule, AuditLog, Direction, ItemComment, Plan, PlanEvent, PlanItem, PlanNote, PlanSnapshot, Project, User,
    UserAvatar,
)
from app.services.legacy_import import import_legacy_database

SNAPSHOT = Path(__file__).resolve().parents[2] / "legacy" / "snapshot" / "puls-2026-10-30.db"


def _count(model, *where):
    return db.session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="нет снимка базы 1.0.1")
def test_real_snapshot_imports_without_loss(app, client):
    legacy = sqlite3.connect(SNAPSHOT)
    legacy_rows = {"task": 0, "milestone": 0, "group": 0}
    legacy_tabs = 0
    legacy_comments = legacy_history = legacy_notes = 0
    for (raw,) in legacy.execute("select data from projects"):
        data = json.loads(raw)
        tabs = data.get("__tabs")
        if not tabs:
            continue
        for tab in tabs["openTabs"]:
            legacy_tabs += 1
            state = json.loads(tabs["tabsData"][tab["id"]])
            legacy_notes += len(state.get("notes") or [])
            for row in state["rows"]:
                legacy_rows[row["type"]] += 1
                legacy_comments += len(row.get("comments") or [])
                legacy_history += len(row.get("changeHistory") or [])
    users = legacy.execute("select count(*) from users").fetchone()[0]
    avatars = legacy.execute("select count(*) from users where avatar != ''").fetchone()[0]
    audit_rows = legacy.execute("select count(*) from audit_log").fetchone()[0]
    rules = legacy.execute("select count(distinct user_id || '-' || coalesce(project_id,'') || '-' || coalesce(group_id,'')) "
                           "from access_rules").fetchone()[0]
    empty_projects = sum(1 for (raw,) in legacy.execute("select data from projects") if not json.loads(raw).get("__tabs"))

    with app.app_context():
        stats, report, errors = import_legacy_database(str(SNAPSHOT))
        assert errors == [], "\n".join(errors)
        assert _count(User) == users
        assert _count(UserAvatar) == avatars
        assert _count(Direction) == legacy.execute("select count(*) from project_groups").fetchone()[0]
        assert _count(Project) == legacy.execute("select count(*) from projects").fetchone()[0]
        assert _count(Plan) == legacy_tabs + empty_projects
        for rtype, n in legacy_rows.items():
            assert _count(PlanItem, PlanItem.type == rtype) == n, rtype
        assert _count(ItemComment) == legacy_comments
        assert _count(PlanEvent, PlanEvent.action == "legacy") == legacy_history
        assert _count(PlanNote) == legacy_notes
        assert _count(AuditLog) == audit_rows
        assert _count(AccessRule) == rules
        assert _count(PlanSnapshot, PlanSnapshot.reason == "legacy_import") == _count(Plan)
        # Пароли и id сохранены — сотрудники входят прежними паролями
        legacy_hashes = dict(legacy.execute("select id, password from users"))
        for user in db.session.execute(select(User)).scalars():
            assert user.password_hash == (legacy_hashes[user.id] or None)

    # Перенесённые данные читаются через API под администратором 1.0.x
    with app.app_context():
        admin = db.session.execute(select(User).where(User.role == "admin")).scalars().first()
        from app.passwords import hash_password

        admin.password_hash = hash_password("test-password-1")
        db.session.commit()
        login = admin.login
    assert client.post("/api/auth/login", json={"login": login, "password": "test-password-1"}).status_code == 200
    projects = client.get("/api/projects").get_json()
    assert len(projects) == legacy.execute("select count(*) from projects").fetchone()[0]
    biggest = max(projects, key=lambda p: p["stats"]["total"] if p.get("stats") else 0)
    project = client.get(f"/api/projects/{biggest['id']}").get_json()
    plan = client.get(f"/api/plans/{project['plans'][0]['id']}").get_json()
    assert len(plan["items"]) > 50
    export = client.get(f"/api/plans/{project['plans'][0]['id']}/export").get_json()
    assert len(export["rows"]) == len(plan["items"])


def test_import_refuses_non_empty_database(app, admin):
    from app.services.legacy_import import LegacyImportError

    if not SNAPSHOT.exists():
        pytest.skip("нет снимка")
    with app.app_context():
        with pytest.raises(LegacyImportError):
            import_legacy_database(str(SNAPSHOT))


def _first_tab_state():
    """Самая насыщенная вкладка: с комментариями, историей, вехами и группами."""
    legacy = sqlite3.connect(SNAPSHOT)
    best = None
    for (raw,) in legacy.execute("select data from projects"):
        tabs = json.loads(raw).get("__tabs")
        if not tabs:
            continue
        for tab in tabs["openTabs"]:
            state = json.loads(tabs["tabsData"][tab["id"]])
            rows = state["rows"]
            if any(r.get("comments") for r in rows) and any(r.get("changeHistory") for r in rows):
                if best is None or len(rows) > len(best["rows"]):
                    best = state
    return best


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="нет снимка базы 1.0.1")
def test_compare_states_detects_any_loss(app):
    """Сверка обязана заметить потерю или искажение данных — иначе ей нельзя доверять."""
    import copy

    from app.services.legacy_import import compare_states

    state = _first_tab_state()
    assert compare_states(state, copy.deepcopy(state)) == []
    task_with_comments = next(r for r in state["rows"] if r.get("comments"))
    task_with_history = next(r for r in state["rows"] if r.get("changeHistory"))
    mutations = [
        lambda s: s["rows"].pop(),
        lambda s: s["rows"][0].update(name="Другое"),
        lambda s: next(r for r in s["rows"] if r["type"] == "task").update(end="2099-01-01"),
        lambda s: next(r for r in s["rows"] if r["type"] == "task").update(status="blocked"),
        lambda s: next(r for r in s["rows"] if r["id"] == task_with_comments["id"])["comments"].pop(),
        lambda s: next(r for r in s["rows"] if r["id"] == task_with_history["id"])["changeHistory"].pop(),
        lambda s: s["assignees"].append({"id": "zz", "name": "Лишний", "role": "", "color": "#000000"}),
        lambda s: s.update(range={"start": "2020-01-01", "end": "2020-12-31"}),
    ]
    for mutate in mutations:
        broken = copy.deepcopy(state)
        mutate(broken)
        assert compare_states(state, broken), mutate


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="нет снимка базы 1.0.1")
def test_legacy_json_file_imports_as_new_plan_and_round_trips(app, admin):
    from tests.helpers import create_project

    state = _first_tab_state()
    project_id, _ = create_project(admin, "Импорт")
    response = admin.post(f"/api/projects/{project_id}/plans", json={"document": state})
    assert response.status_code == 201, response.get_json()
    plan = response.get_json()
    doc = admin.get(f"/api/plans/{plan['id']}").get_json()
    assert len(doc["items"]) == len(state["rows"])
    exported = admin.get(f"/api/plans/{plan['id']}/export").get_json()
    from app.services.legacy_import import compare_states

    problems = [p for p in compare_states(state, exported) if "история" not in p]
    assert problems == []
    # Повторный импорт выгрузки даёт тот же план (порядок строк сохраняется через sortIndex)
    again = admin.post(f"/api/projects/{project_id}/plans", json={"document": exported}).get_json()
    doc2 = admin.get(f"/api/plans/{again['id']}").get_json()
    assert [i["key"] for i in doc2["items"]] == [i["key"] for i in doc["items"]]
