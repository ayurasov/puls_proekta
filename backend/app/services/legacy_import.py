"""Перенос базы 1.0.x (SQLite puls.db) в схему 0.2 без потерь (ADR-0005).

Что переносится:

* сотрудники с теми же id, логинами, ролями, хэшами паролей (bcrypt) и фото;
* направления (project_groups) и проекты с теми же id, порядком и авторами;
* все вкладки-версии каждого проекта — как планы проекта в том же порядке;
* строки, комментарии (с авторами и датами), история изменений строк, пометки,
  исполнители, настройки графика каждой вкладки;
* правила доступа (повторы объединяются с наибольшим уровнем);
* реестр изменений полностью; настройки LDAP.

Не переносится: сессии (после перехода нужно войти заново) и активная вкладка
(теперь у каждого сотрудника своя).

После переноса выполняется сверка: каждый план выгружается обратно в формат 1.0.x и
сравнивается с исходной вкладкой поле за полем. Любое расхождение, кроме
задокументированных нормализаций, отменяет перенос целиком.
"""

import json
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select, text

from app.api.avatars import decode_data_url
from app.extensions import db
from app.models import (
    AccessRule, AppSetting, AuditLog, Direction, ItemComment, Plan, PlanAssignee, PlanEvent, PlanItem, PlanNote,
    PlanSnapshot, Project, User, UserAvatar, utcnow,
)
from app.services.legacy_format import (
    ImportReport, format_legacy_datetime, legacy_state_to_document, plan_to_legacy_state,
)
from app.services.plan_doc import create_snapshot
from app.services.plan_schema import convert_field
from app.security import LEVEL_ORDER

ROLES = {"admin", "manager", "editor", "viewer"}


class LegacyImportError(RuntimeError):
    pass


def _clean(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def open_legacy(path):
    """Копия базы (вместе с -wal/-shm) во временный каталог: исходные файлы не трогаем."""
    source = Path(path)
    if not source.exists():
        raise LegacyImportError(f"Файл не найден: {source}")
    tmp = Path(tempfile.mkdtemp(prefix="puls-legacy-"))
    target = tmp / "puls.db"
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(source) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(target) + suffix))
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        raise LegacyImportError(f"База 1.0.x повреждена: {check}")
    return conn, tmp


def _table_exists(conn, name):
    return conn.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone() is not None


def _target_is_empty():
    for model in (User, Project, Direction, AuditLog):
        if db.session.execute(select(func.count()).select_from(model)).scalar_one():
            return False
    return True


def _wipe_target():
    for model in (PlanSnapshot, PlanEvent, ItemComment, PlanNote, PlanItem, PlanAssignee, Plan, AccessRule, Project,
                  Direction, UserAvatar, AuditLog, AppSetting):
        db.session.execute(model.__table__.delete())
    db.session.execute(text("DELETE FROM plan_presence"))
    db.session.execute(text("DELETE FROM user_sessions"))
    db.session.execute(User.__table__.delete())
    db.session.flush()


def _reset_sequences():
    if db.engine.dialect.name != "postgresql":
        return
    for table in ("users", "directions", "projects", "plans", "plan_items", "plan_notes", "plan_assignees",
                  "item_comments", "plan_events", "plan_snapshots", "access_rules", "audit_log"):
        # setval не откатывается вместе с транзакцией (репетиция --dry-run): счётчик только растёт,
        # иначе после отката он оказался бы ниже уже занятых id
        db.session.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), GREATEST("
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, nextval(pg_get_serial_sequence('{table}', 'id'))), false)"
        ))


class LegacyImporter:
    def __init__(self, conn, report=None):
        self.conn = conn
        self.report = report or ImportReport()
        self.stats = {}
        self.users_by_name = {}
        self.verify_errors = []

    def count(self, key, n=1):
        self.stats[key] = self.stats.get(key, 0) + n

    # ---------- сотрудники ----------
    def import_users(self):
        rows = self.conn.execute("select * from users order by id").fetchall()
        names = {}
        for row in rows:
            keys = row.keys()
            role = row["role"] if row["role"] in ROLES else "viewer"
            if role != row["role"]:
                self.report.add(f"Сотрудник {row['username']}: неизвестная роль «{row['role']}» заменена на «Наблюдатель»")
            name = _clean(row["name"]) or row["username"]
            for field in ("name", "email") + (("position",) if "position" in keys else ()):
                raw = row[field] or ""
                if raw != _clean(raw):
                    self.count("whitespace_normalized")
            user = User(
                id=row["id"], login=row["username"].strip(), password_hash=row["password"] or None, name=name,
                email=_clean(row["email"]), position=_clean(row["position"]) if "position" in keys else "",
                role=role, is_active=bool(row["active"]),
                auth_source=row["auth_source"] if "auth_source" in keys and row["auth_source"] in ("local", "ldap") else "local",
                created_at=_parse_iso(row["created_at"]) or utcnow(), updated_at=utcnow(),
            )
            db.session.add(user)
            names.setdefault(name, []).append(user)
            avatar = row["avatar"] if "avatar" in keys else ""
            if avatar:
                decoded = decode_data_url(avatar)
                if decoded is None:
                    self.report.add(f"Фото сотрудника {row['username']} не распознано как изображение — не перенесено")
                else:
                    import hashlib

                    content_type, data = decoded
                    db.session.add(UserAvatar(user_id=user.id, content_type=content_type, data=data, size=len(data),
                                              sha256=hashlib.sha256(data).hexdigest()))
                    user.avatar_version = 1
                    self.count("avatars")
            self.count("users")
        self.users_by_name = {name: users[0].id for name, users in names.items() if len(users) == 1}
        db.session.flush()

    def user_id_by_name(self, name):
        return self.users_by_name.get(_clean(name))

    # ---------- направления и проекты ----------
    def import_directions(self):
        for index, row in enumerate(self.conn.execute("select * from project_groups order by id").fetchall()):
            db.session.add(Direction(id=row["id"], name=_clean(row["name"]) or f"Направление {row['id']}",
                                     description=row["description"] or "", color=row["color"] or "blue",
                                     position=index + 1))
            self.count("directions")
        db.session.flush()

    def import_projects(self):
        directions = {d.id for d in db.session.execute(select(Direction)).scalars()}
        users = {u.id for u in db.session.execute(select(User)).scalars()}
        rows = self.conn.execute("select * from projects order by id").fetchall()
        for row in rows:
            direction_id = row["group_id"] if row["group_id"] in directions else None
            if row["group_id"] and direction_id is None:
                self.report.add(f"Проект «{row['title']}»: направление #{row['group_id']} отсутствует — без направления")
            project = Project(
                id=row["id"], title=_clean(row["title"]) or f"Проект {row['id']}", subtitle=row["subtitle"] or "",
                direction_id=direction_id, position=float(row["sort_order"] or 0),
                created_by_id=row["created_by"] if row["created_by"] in users else None,
                created_at=_parse_iso(row["created_at"]) or utcnow(),
                updated_at=_parse_iso(row["updated_at"]) or utcnow(),
            )
            db.session.add(project)
            db.session.flush()
            self.count("projects")
            self.import_project_plans(project, row["data"])

    def _tabs(self, project, raw):
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            self.report.add(f"Проект «{project.title}»: данные не читаются как JSON — создан пустой план")
            data = {}
        tabs = data.get("__tabs") if isinstance(data, dict) else None
        result = []
        if isinstance(tabs, dict) and tabs.get("openTabs"):
            tabs_data = tabs.get("tabsData") or {}
            for tab in tabs["openTabs"]:
                state_raw = tabs_data.get(tab.get("id"))
                if state_raw:
                    state = json.loads(state_raw) if isinstance(state_raw, str) else state_raw
                elif tab.get("id") == tabs.get("activeTabId"):
                    state = {k: v for k, v in data.items() if k != "__tabs"}
                    self.report.add(f"Проект «{project.title}», вкладка «{tab.get('title')}»: данных вкладки нет — "
                                    "взяты данные активной вкладки")
                else:
                    state = {}
                    self.report.add(f"Проект «{project.title}», вкладка «{tab.get('title')}»: данных нет — пустой план")
                result.append((tab.get("id"), tab.get("title") or project.title, state))
            active = tabs.get("activeTabId")
            top = {k: v for k, v in data.items() if k not in ("__tabs", "history")}
            active_state = next((s for tid, _t, s in result if tid == active), None)
            if active_state is not None and top:
                a = {k: v for k, v in active_state.items() if k != "history"}
                if json.dumps(a, sort_keys=True) != json.dumps(top, sort_keys=True):
                    self.report.add(f"Проект «{project.title}»: копия активной вкладки на верхнем уровне отличается "
                                    "от данных вкладки — перенесены данные вкладки")
        elif isinstance(data, dict) and data.get("rows") is not None:
            result.append((((data.get("project") or {}).get("id")), project.title, data))
        else:
            result.append((None, project.title, {}))
        return result

    def import_project_plans(self, project, raw):
        for position, (tab_id, title, state) in enumerate(self._tabs(project, raw), start=1):
            self.import_plan(project, position, tab_id, title, state)

    def import_plan(self, project, position, tab_id, title, state):
        doc, comments, events, _ = legacy_state_to_document(state, title=_clean(title) or project.title,
                                                            report=self.report)
        stamp = project.updated_at
        key = tab_id or f"p_legacy_{project.id}_{position}"
        if db.session.execute(select(Plan.id).where(Plan.key == key)).first():
            key = f"{key}_{project.id}"
        plan = Plan(project_id=project.id, key=key, title=doc["plan"]["title"][:500],
                    subtitle=doc["plan"]["subtitle"][:1000], position=float(position),
                    settings=doc["plan"]["settings"], revision=1, meta_rev=1,
                    next_row_seq=doc["plan"]["nextRowSeq"], created_at=project.created_at, updated_at=stamp,
                    created_by_id=project.created_by_id)
        db.session.add(plan)
        db.session.flush()
        self.count("plans")
        common = {"plan_id": plan.id, "created_at": stamp, "updated_at": stamp, "updated_rev": 1}

        for a in doc["assignees"]:
            user_id = self.user_id_by_name(a["name"])
            db.session.add(PlanAssignee(key=a["key"], name=a["name"], role=a["role"], color=a["color"],
                                        position=a["position"], user_id=user_id, **common))
            self.count("assignees")
            if user_id:
                self.count("assignees_linked_to_users")

        for entry in doc["items"]:
            item = PlanItem(key=entry["key"], type=entry["type"], name=entry["name"], parent_key=entry.get("parentKey"),
                            position=entry["position"], extra=entry.get("extra"), comment_count=0, **common)
            for api_name, value in entry.items():
                if api_name in ("key", "type", "name", "parentKey", "position", "extra"):
                    continue
                attr, converted = convert_field(api_name, value)
                setattr(item, attr, converted)
            db.session.add(item)
            self.count(f"items_{entry['type']}")
        for n in doc["notes"]:
            db.session.add(PlanNote(key=n["key"], date=convert_field("date", n["date"])[1], text=n["text"],
                                    row_key=n["rowKey"], color=n["color"], show_date=n["showDate"], **common))
            self.count("notes")
        db.session.flush()

        by_item = {}
        for c in comments:
            by_item.setdefault(c["itemKey"], []).append(c)
        for item_key, entries in by_item.items():
            created = []
            for c in entries:
                row = ItemComment(plan_id=plan.id, item_key=item_key, author_id=self.user_id_by_name(c["authorName"]),
                                  author_name=c["authorName"], text=c["text"], created_at=c["createdAt"] or stamp,
                                  extra=c["extra"])
                db.session.add(row)
                created.append((c, row))
                self.count("comments")
            db.session.flush()
            for c, row in created:
                idx = c["replyToIndex"]
                if isinstance(idx, int) and 0 <= idx < len(created):
                    row.reply_to_id = created[idx][1].id
            db.session.execute(
                PlanItem.__table__.update()
                .where(PlanItem.plan_id == plan.id, PlanItem.key == item_key)
                .values(comment_count=len(created))
            )

        for e in events:
            summary = e["action"] + (f": {e['details']}" if e["details"] else "")
            db.session.add(PlanEvent(plan_id=plan.id, revision=1, entity="item", entity_key=e["itemKey"],
                                     action="legacy", user_id=self.user_id_by_name(e["userName"]),
                                     user_name=e["userName"], created_at=e["createdAt"] or stamp,
                                     summary=summary, changes={"legacy": e["raw"]}))
            self.count("history_entries")
        db.session.add(PlanEvent(plan_id=plan.id, revision=1, entity="plan", action="import", user_id=None,
                                 user_name="перенос данных", created_at=utcnow(),
                                 summary="План перенесён из версии 1.0.1 без изменений"))
        db.session.flush()
        snapshot = create_snapshot(plan, None, "legacy_import", "Состояние на момент переноса из 1.0.1")
        snapshot.user_name = "перенос данных"
        self._verify_plan(project, plan, state, title)

    # ---------- доступы, реестр, настройки ----------
    def import_access(self):
        users = {u.id for u in db.session.execute(select(User)).scalars()}
        projects = {p.id for p in db.session.execute(select(Project)).scalars()}
        directions = {d.id for d in db.session.execute(select(Direction)).scalars()}
        best = {}
        for row in self.conn.execute("select * from access_rules order by id").fetchall():
            if row["user_id"] not in users:
                self.report.add(f"Правило доступа #{row['id']}: сотрудник #{row['user_id']} отсутствует — пропущено")
                continue
            if row["project_id"]:
                target = ("project_id", row["project_id"])
                if row["project_id"] not in projects:
                    self.report.add(f"Правило доступа #{row['id']}: проект #{row['project_id']} отсутствует — пропущено")
                    continue
            elif row["group_id"]:
                target = ("direction_id", row["group_id"])
                if row["group_id"] not in directions:
                    self.report.add(f"Правило доступа #{row['id']}: направление #{row['group_id']} отсутствует — пропущено")
                    continue
            else:
                self.report.add(f"Правило доступа #{row['id']} без проекта и направления — пропущено")
                continue
            level = row["level"] if row["level"] in ("view", "edit", "manage") else "view"
            key = (row["user_id"],) + target
            if key in best:
                self.count("access_duplicates_merged")
                previous = best[key]
                if LEVEL_ORDER[level] > LEVEL_ORDER[previous["level"]]:
                    previous["level"] = level
                continue
            best[key] = {"id": row["id"], "user_id": row["user_id"], target[0]: target[1], "level": level}
        for values in best.values():
            db.session.add(AccessRule(**values))
            self.count("access_rules")
        db.session.flush()

    def import_audit(self):
        project_ref = re.compile(r"^project #(\d+)")
        for row in self.conn.execute("select * from audit_log order by id").fetchall():
            match = project_ref.match(row["entity"] or "")
            created = None
            try:
                created = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                created = utcnow()
            db.session.add(AuditLog(
                id=row["id"], created_at=created, user_id=row["user_id"], user_name=row["user_name"] or "",
                project_id=int(match.group(1)) if match else None,
                project_title="" if row["project_title"] in (None, "—") else row["project_title"],
                section=row["page"] or "", action=row["action"] or "", entity=row["entity"] or "",
                field=row["field"] or "", old_value=row["old_value"] or "", new_value=row["new_value"] or "",
            ))
            self.count("audit_rows")
        db.session.flush()

    def import_settings(self):
        if not _table_exists(self.conn, "app_settings"):
            return
        for row in self.conn.execute("select key, value from app_settings").fetchall():
            try:
                value = json.loads(row["value"])
            except ValueError:
                value = row["value"]
            db.session.add(AppSetting(key=row["key"], value=value))
            self.count("settings")

    # ---------- сверка ----------
    def _verify_plan(self, project, plan, original, title):
        items = db.session.execute(select(PlanItem).where(PlanItem.plan_id == plan.id)).scalars().all()
        notes = db.session.execute(select(PlanNote).where(PlanNote.plan_id == plan.id)).scalars().all()
        assignees = db.session.execute(select(PlanAssignee).where(PlanAssignee.plan_id == plan.id)).scalars().all()
        comments = {}
        for c in db.session.execute(select(ItemComment).where(ItemComment.plan_id == plan.id)
                                    .order_by(ItemComment.id)).scalars():
            comments.setdefault(c.item_key, []).append(c)
        events = {}
        for e in db.session.execute(select(PlanEvent).where(PlanEvent.plan_id == plan.id, PlanEvent.action == "legacy")
                                    .order_by(PlanEvent.id)).scalars():
            events.setdefault(e.entity_key, []).append(e)
        exported = plan_to_legacy_state(plan, items, notes, assignees, comments, events)
        problems = compare_states(original, exported)
        for problem in problems:
            self.verify_errors.append(f"Проект «{project.title}», план «{title}»: {problem}")

    def run(self):
        self.import_users()
        self.import_directions()
        self.import_projects()
        self.import_access()
        self.import_audit()
        self.import_settings()
        _reset_sequences()
        return self.stats


def _norm_text(value):
    return _clean(value)


def compare_states(original, exported):
    """Расхождения между исходной вкладкой 1.0.x и выгрузкой перенесённого плана."""
    problems = []
    orig_rows = {r["id"]: r for r in original.get("rows") or [] if isinstance(r, dict) and r.get("id")}
    new_rows = {r["id"]: r for r in exported["rows"]}
    if set(orig_rows) != set(new_rows):
        problems.append(f"разный состав строк: потеряны {sorted(set(orig_rows) - set(new_rows))}, "
                        f"лишние {sorted(set(new_rows) - set(orig_rows))}")
    task_fields = ("start", "end", "endMode", "durationType", "progress", "offset", "status", "dependencyId",
                   "dependencyType", "depLag", "assigneeId", "color")
    for key, orig in orig_rows.items():
        new = new_rows.get(key)
        if new is None:
            continue
        rtype = orig.get("type")
        if new["type"] != rtype:
            problems.append(f"строка {key}: тип {rtype} → {new['type']}")
        if _norm_text(orig.get("name")) != _norm_text(new["name"]) and _norm_text(orig.get("name")):
            problems.append(f"строка {key}: название")
        orig_parent = orig.get("parentId") or orig.get("groupId") or ""
        new_parent = new.get("parentId") or new.get("groupId") or ""
        if orig_parent != new_parent and orig_parent in orig_rows:
            problems.append(f"строка {key}: родитель {orig_parent} → {new_parent}")
        if (orig.get("groupId") or "") != (new.get("groupId") or "") and orig.get("groupId") in orig_rows \
                and rtype != "group":
            problems.append(f"строка {key}: группа {orig.get('groupId')} → {new.get('groupId')}")
        if rtype == "task":
            for field in task_fields:
                a, b = orig.get(field), new.get(field)
                if field in ("progress", "offset", "depLag"):
                    a, b = int(round(float(a or 0))), int(b or 0)
                if field == "dependencyId" and a and a not in orig_rows:
                    continue
                if (a or "") != (b or ""):
                    problems.append(f"задача {key}: {field} «{a}» → «{b}»")
            for field in ("category", "barText"):
                if _norm_text(orig.get(field)) != _norm_text(new.get(field)):
                    problems.append(f"задача {key}: {field}")
            if (orig.get("jiraUrl") or "").strip() != (new.get("jiraUrl") or "").strip():
                problems.append(f"задача {key}: jiraUrl")
            if (orig.get("completedDate") or "") != (new.get("completedDate") or ""):
                problems.append(f"задача {key}: completedDate")
            oc = [(c.get("text"), _norm_text(c.get("author")), c.get("date"), c.get("replyTo"))
                  for c in orig.get("comments") or []]
            nc = [(c.get("text"), _norm_text(c.get("author")), c.get("date"), c.get("replyTo"))
                  for c in new.get("comments") or []]
            if oc != nc:
                problems.append(f"задача {key}: комментарии ({len(oc)} → {len(nc)})")
            if (orig.get("changeHistory") or []) != (new.get("changeHistory") or []):
                problems.append(f"задача {key}: история ({len(orig.get('changeHistory') or [])} → "
                                f"{len(new.get('changeHistory') or [])})")
        elif rtype == "milestone":
            for field in ("date", "lineStyle", "assigneeId"):
                if (orig.get(field) or "") != (new.get(field) or ""):
                    problems.append(f"веха {key}: {field}")
            if _norm_text(orig.get("comment")) != _norm_text(new.get("comment")):
                problems.append(f"веха {key}: comment")
            if (orig.get("color") or "teal") != new.get("color"):
                problems.append(f"веха {key}: color")
            if (orig.get("lineColor") or orig.get("color") or "teal") != new.get("lineColor"):
                problems.append(f"веха {key}: lineColor")
        elif rtype == "group":
            if (orig.get("color") or "slate") != new.get("color"):
                problems.append(f"группа {key}: color")
    on = [(n.get("id"), n.get("date"), (n.get("text") or "").strip(), n.get("rowId") or "", n.get("color") or "orange",
           bool(n.get("showDate"))) for n in original.get("notes") or []]
    nn = [(n["id"], n["date"], n["text"], n["rowId"], n["color"], n["showDate"]) for n in exported["notes"]]
    if sorted(on) != sorted(nn):
        problems.append("пометки отличаются")
    oa = [(a.get("id"), _norm_text(a.get("name")), _norm_text(a.get("role")), a.get("color"))
          for a in original.get("assignees") or []]
    na = [(a["id"], a["name"], a["role"], a["color"]) for a in exported["assignees"]]
    if oa != na:
        problems.append("исполнители отличаются")
    for field in ("range", "scale", "rowHeight"):
        if field in original and original[field] != exported[field]:
            problems.append(f"настройка {field}")
    if "zoom" in original and float(original["zoom"]) != float(exported["zoom"]):
        problems.append("настройка zoom")
    today = original.get("today") or {}
    if today.get("auto") is False and today.get("date") != exported["today"]["date"]:
        problems.append("зафиксированная дата «сегодня»")
    orig_settings = original.get("settings") or {}
    for key, value in orig_settings.items():
        if key in exported["settings"] and exported["settings"][key] != value:
            problems.append(f"настройка settings.{key}")
    def collapsed(state):
        return {k for k, v in (state.get("collapsed") or {}).items() if v}

    if collapsed(original) != collapsed(exported):
        problems.append("свёрнутые строки")
    return problems


def import_legacy_database(sqlite_path, replace=False):
    """Перенос в текущую БД приложения (внутри app_context). Возвращает (stats, report, errors)."""
    conn, tmp = open_legacy(sqlite_path)
    try:
        if not _target_is_empty():
            if not replace:
                raise LegacyImportError("В базе 0.2 уже есть данные. Перенос выполняется в пустую базу "
                                        "(для повторной репетиции — флаг --replace)")
            _wipe_target()
        importer = LegacyImporter(conn)
        stats = importer.run()
        if importer.verify_errors:
            db.session.rollback()
            return stats, importer.report, importer.verify_errors
        db.session.commit()
        return stats, importer.report, []
    except Exception:
        db.session.rollback()
        raise
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def render_report(stats, report, errors, source):
    labels = {
        "users": "Сотрудники", "avatars": "Фото сотрудников", "directions": "Направления", "projects": "Проекты",
        "plans": "Планы (вкладки-версии)", "items_task": "Задачи", "items_milestone": "Вехи", "items_group": "Группы",
        "notes": "Пометки", "assignees": "Исполнители планов", "assignees_linked_to_users": "…из них связаны с сотрудниками",
        "comments": "Комментарии", "history_entries": "Записи истории строк", "access_rules": "Правила доступа",
        "access_duplicates_merged": "Объединено повторов правил доступа", "audit_rows": "Записи реестра изменений",
        "settings": "Настройки", "whitespace_normalized": "Убраны лишние пробелы в ФИО/email/должностях",
    }
    lines = ["# Отчёт о переносе данных 1.0.x → 0.2", "", f"Источник: `{source}`",
             f"Время: {format_legacy_datetime(utcnow())} (МСК)", "", "## Перенесено", "", "| Что | Количество |",
             "| --- | ---: |"]
    for key, label in labels.items():
        if key in stats:
            lines.append(f"| {label} | {stats[key]} |")
    lines += ["", "## Сверка", ""]
    if errors:
        lines.append(f"**Расхождений: {len(errors)}. Перенос отменён, база 0.2 не изменена.**")
        lines.append("")
        lines += [f"- {e}" for e in errors]
    else:
        lines.append("Каждый план выгружен обратно в формат 1.0.x и совпал с исходной вкладкой: строки, даты, "
                     "статусы, связи, исполнители, комментарии, история, пометки, настройки.")
    lines += ["", "## Замечания", ""]
    lines += [f"- {n}" for n in report.notes] or ["- нет"]
    return "\n".join(lines) + "\n"
