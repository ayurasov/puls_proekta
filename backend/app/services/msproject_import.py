"""Импорт MS Project в «Пульс» (ADR-0012): план загрузки и применение для новых проекта и плана, замены плана и
обновления плана, который ведётся в MS Project (мастер-копия).

Чтение и сопоставление — чистые функции `app.services.msproject`; здесь — то, что зависит от базы: сотрудники для
связи ресурсов, адреса Jira, производственный календарь, создание проекта и плана, снимок до изменения, правка плана
одной ревизией (без ограничения 2000 операций — импорт выполняет сервер, как перенос 1.0.1 и копию плана).
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select

from app.extensions import db
from app.models import CalendarDay, ItemComment, PlanEvent, PlanItem, User, utcnow
from app.security import ApiError
from app.services import jira as jira_service
from app.services.msproject import MsProjectError, diff_plan, map_project, read_mspdi, to_ops
from app.services.plan_doc import create_snapshot, snapshot_document
from app.services.plan_ops import PlanEditor

TARGETS = ("project", "plan", "replace", "update")
MODES = ("once", "master")
TREE_LIMIT = 300        # строк дерева в плане загрузки
CHANGES_LIMIT = 500     # строк каждого списка изменений в плане обновления


def clean_options(raw, target=None, plan=None):
    """Параметры загрузки из интерфейса → проверенные (лишнее отбрасывается). Без явного режима обновление
    сохраняет режим плана: мастер-копия остаётся мастер-копией, разовый перенос — разовым."""
    raw = raw if isinstance(raw, dict) else {}
    category = raw.get("categoryField")
    if raw.get("mode") in MODES:
        mode = raw["mode"]
    elif target == "update" and plan is not None:
        mode = "master" if ((plan.settings or {}).get("source") or {}).get("master") else "once"
    else:
        mode = "once"
    return {
        "mode": mode,
        "durations": raw.get("durations") if raw.get("durations") in ("dates", "working") else "dates",
        "baseline": raw.get("baseline") if raw.get("baseline") in ("current", "baseline") else "current",
        "categoryField": str(category)[:100] if category not in (None, "") else None,
        "deleteMissing": raw.get("deleteMissing") is True,
    }


def _context(viewer=None):
    """Сотрудники, адреса Jira и производственный календарь — для сопоставления. Ресурсы связываются с
    сотрудниками по email только у администратора: иначе план загрузки отвечал бы, чей это email (адреса
    сотрудников видит только администратор); остальным — по ФИО, как в «Структуре компании»."""
    by_email = viewer is not None and viewer.role == "admin"
    users = [{"id": u.id, "name": u.name, "email": u.email if by_email else None}
             for u in db.session.execute(select(User).where(User.is_active.is_(True))).scalars()]
    conn = jira_service.get_connection()
    jira_urls = [conn["baseUrl"], *(conn.get("linkUrls") or [])] if conn and conn.get("baseUrl") else []
    days = db.session.execute(select(CalendarDay)).scalars().all()
    calendar = {"holidays": [d.day.isoformat() for d in days if d.kind == "holiday"],
                "workdays": [d.day.isoformat() for d in days if d.kind == "workday"]} if days else None
    return {"users": users, "jiraBaseUrls": jira_urls, "calendar": calendar}


def fingerprint(data, options):
    digest = hashlib.sha256(data)
    digest.update(repr(sorted(options.items())).encode())
    return digest.hexdigest()


def read_document(data, options, file_name="", viewer=None):
    """Файл → документ загрузки «Пульса». Ошибки файла — 400 с текстом для сотрудника."""
    try:
        project = read_mspdi(data)
        # Сопоставление тоже может отклонить файл (даты и длительности вне допустимого) — так же 400, а не 500
        doc = map_project(project, {**_context(viewer), **{k: v for k, v in options.items()
                                                     if k in ("durations", "baseline", "categoryField")}})
    except MsProjectError as exc:
        raise ApiError(400, "msproject_invalid", exc.message) from None
    source = dict(doc["settings"].get("source") or {})
    source.update({"kind": "msproject", "master": options["mode"] == "master", "fileName": (file_name or "")[:255],
                   "importedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")})
    doc["settings"]["source"] = source
    return doc


def _tree(doc):
    depth = {}
    rows = []
    for item in doc["items"][:TREE_LIMIT]:
        level = depth.get(item.get("parentKey"), -1) + 1
        depth[item["key"]] = level
        msp = (item.get("extra") or {}).get("msp") or {}
        rows.append({"key": item["key"], "type": item["type"], "name": item["name"], "depth": level,
                     "start": item.get("start") or item.get("date"), "end": item.get("end") or item.get("date"),
                     "wbs": msp.get("wbs") or msp.get("outlineNumber")})
    return rows


def _assignee_matches(doc):
    names = {u.id: u.name for u in db.session.execute(
        select(User).where(User.id.in_([a["userId"] for a in doc["assignees"] if a.get("userId")]))).scalars()}
    return [{"key": a["key"], "name": a["name"], "userId": a.get("userId"), "userName": names.get(a.get("userId"))}
            for a in doc["assignees"]]


def preview(data, options, target, plan=None, file_name="", viewer=None):
    """План загрузки: что будет создано или изменено, предупреждения, дерево первых строк."""
    doc = read_document(data, options, file_name, viewer)
    out = {
        "fingerprint": fingerprint(data, options), "project": doc["project"], "stats": doc["stats"],
        "warnings": doc["warnings"], "customFields": doc.get("customFields") or [], "tree": _tree(doc),
        "assignees": _assignee_matches(doc), "options": options, "target": target,
        "truncated": len(doc["items"]) > TREE_LIMIT,
    }
    if target == "update":
        changes = diff_plan(snapshot_document(plan), doc, reserved_keys=_taken_keys(plan)).to_dict()
        out["changes"] = _short_changes(changes)
        out["planRevision"] = plan.revision
        # Предупреждения плана обновления (другой проект, сменившие тип строки, удалённые в «Пульсе»…) — первыми,
        # в общем списке, который показывает окно загрузки
        out["warnings"] = [*changes.get("warnings", []), *out["warnings"]]
    elif target == "replace":
        out["planRevision"] = plan.revision
        out["replacing"] = db.session.query(PlanItem).filter(
            PlanItem.plan_id == plan.id, PlanItem.deleted_at.is_(None)).count()
    return out


def _short_changes(changes):
    """План обновления для интерфейса: списки до CHANGES_LIMIT строк и числа."""
    short = {k: v[:CHANGES_LIMIT] if isinstance(v, list) else v for k, v in changes.items() if k != "keyMap"}
    short["links"] = {k: v[:CHANGES_LIMIT] for k, v in (changes.get("links") or {}).items()}
    return short


# ---------- применение ----------

def _snapshot(plan, user, label):
    """Снимок плана перед заменой или обновлением — его можно вернуть в «Версиях» («Перед импортом»). Источник
    плана записывается в снимок явно, даже пустой: иначе восстановление оставило бы план мастер-копией."""
    snapshot = create_snapshot(plan, user, reason="before_import", label=label)
    data = dict(snapshot.data)
    data["plan"] = dict(data["plan"], settings={**data["plan"]["settings"],
                                                "source": (plan.settings or {}).get("source")})
    snapshot.data = data


def snapshot_after(plan, user, file_name):
    """Версия плана сразу после импорта («После импорта»): к ней можно вернуться после правок в «Пульсе» и
    сравнить с ней следующее обновление из файла (ADR-0012, раздел 2)."""
    db.session.flush()
    return create_snapshot(plan, user, reason="after_import", label=f"Импорт из MS Project: {file_name}")


def _apply_ops(plan, user, ops, summary):
    editor = PlanEditor(plan, user)
    # Связь, которую нельзя создать (строку пропустили), не останавливает загрузку — она попадает в исправления
    editor.skip_invalid_links = True
    editor.trusted = True  # пакет собирает сервер: идентификаторы строк в MS Project (extra.msp) записываются
    events = editor.apply(ops, max_ops=None) if ops else []
    # Изменения строк — в историю строк (новая строка, сроки, название…), как у обычной правки
    db.session.add_all(events)
    plan.revision = editor.revision
    plan.updated_at = editor.now
    plan.updated_by_id = user.id
    db.session.add(PlanEvent(plan_id=plan.id, revision=plan.revision, entity="plan", action="import",
                             user_id=user.id, user_name=user.name, summary=summary))
    return editor


def document_ops(doc):
    """Документ загрузки → операции создания: исполнители, строки (родители раньше вложенных), связи."""
    ops = [{"op": "assignee.create", "assignee": a} for a in doc.get("assignees", [])]
    ops += [{"op": "item.create", "item": item} for item in doc.get("items", [])]
    ops += [{"op": "link.create", "link": link} for link in doc.get("links", [])]
    return ops


def _add_comments(plan, user, comments):
    """Заметки задач MS Project — первым комментарием строки."""
    alive = {i.key: i for i in db.session.execute(select(PlanItem).where(
        PlanItem.plan_id == plan.id, PlanItem.deleted_at.is_(None))).scalars()}
    for entry in comments:
        item = alive.get(entry.get("itemKey"))
        if item is None or not entry.get("text"):
            continue
        db.session.add(ItemComment(plan_id=plan.id, item_key=item.key, author_id=user.id, author_name=user.name,
                                   text=entry["text"][:20000], created_at=utcnow(), extra={"source": "msproject"}))
        item.comment_count = (item.comment_count or 0) + 1


def settings_for(doc, base=None):
    """Настройки плана из документа загрузки: шкала, категории, источник; остальное — как было."""
    settings = dict(base or {})
    for key in ("range", "categories", "source"):
        if doc["settings"].get(key) is not None:
            settings[key] = doc["settings"][key]
    return settings


def fill(plan, user, doc, summary):
    _apply_ops(plan, user, document_ops(doc), summary)
    _add_comments(plan, user, doc.get("comments") or [])


def replace_plan(plan, user, doc, file_name):
    """Заменить содержимое плана: всё прежнее — в корзину плана одной правкой, затем строки из файла."""
    _snapshot(plan, user, f"До импорта из MS Project: {file_name}")
    current = snapshot_document(plan)
    ops = [{"op": "link.delete", "key": link["key"]} for link in current.get("links", [])]
    ops += [{"op": "note.delete", "key": note["key"]} for note in current.get("notes", [])]
    ops += [{"op": "item.delete", "key": item["key"]} for item in current.get("items", [])]
    used = {a["name"].strip().lower(): a["key"] for a in current.get("assignees", [])}
    remap = {}
    new_assignees = []
    for assignee in doc.get("assignees", []):
        existing = used.get(assignee["name"].strip().lower())
        if existing:
            remap[assignee["key"]] = existing
        else:
            new_assignees.append(assignee)
    taken = _taken_keys(plan)
    keys = _free_keys([*doc["items"], *doc.get("links", [])], taken)
    # Новый исполнитель не занимает ключ прежнего: иначе создание приняли бы за повтор (переименование прежнего)
    # или отклонили бы как занятый ключ, если прежнего завёл другой пользователь
    assignee_keys = _free_keys(new_assignees, taken)
    remap.update(assignee_keys)
    items = []
    for item in doc["items"]:
        item = dict(item, key=keys[item["key"]], parentKey=keys.get(item.get("parentKey")) if item.get("parentKey") else None)
        if item.get("assigneeKey"):
            item["assigneeKey"] = remap.get(item["assigneeKey"], item["assigneeKey"])
        items.append(item)
    links = [dict(link, key=keys.get(link["key"], link["key"]), **{"from": keys[link["from"]], "to": keys[link["to"]]})
             for link in doc.get("links", [])]
    ops += [{"op": "assignee.create", "assignee": dict(a, key=assignee_keys[a["key"]])} for a in new_assignees]
    ops += [{"op": "item.create", "item": i} for i in items]
    ops += [{"op": "link.create", "link": link} for link in links]
    ops.append({"op": "plan.update", "fields": {"settings": settings_for(doc, plan.settings)}})
    _apply_ops(plan, user, ops, f"Содержимое заменено импортом из MS Project: {file_name} ({len(items)} строк)")
    _add_comments(plan, user, [dict(c, itemKey=keys.get(c["itemKey"], c["itemKey"])) for c in doc.get("comments") or []])


def _taken_keys(plan):
    """Ключи строк, связей и исполнителей плана, в том числе удалённых (в корзине плана), — новые их не занимают:
    иначе создание совпало бы с удалённой строкой и было бы принято за повтор пакета."""
    from app.models import PlanAssignee, PlanLink

    taken = set()
    for model in (PlanItem, PlanLink, PlanAssignee):
        taken |= set(db.session.execute(select(model.key).where(model.plan_id == plan.id)).scalars())
    return taken


def _free_keys(entries, taken):
    """Ключи записей документа, не занятые в плане (удалённые строки плана остаются в корзине): старый ключ → новый."""
    keys = {}
    for entry in entries:
        key = entry["key"]
        n = 1
        candidate = key
        while candidate in taken:
            n += 1
            candidate = f"{key}_{n}"
        taken.add(candidate)
        keys[key] = candidate
    return keys


def update_plan(plan, user, doc, options, file_name):
    """Обновить план, который ведётся в MS Project: строки по GUID и UID, только поля MS Project."""
    _snapshot(plan, user, f"До обновления из MS Project: {file_name}")
    changes = diff_plan(snapshot_document(plan), doc, reserved_keys=_taken_keys(plan))
    ops = to_ops(changes, {"deleteMissing": options["deleteMissing"]})
    settings = {"source": doc["settings"]["source"]}
    categories = list((plan.settings or {}).get("categories") or [])
    added = [c for c in doc["settings"].get("categories") or [] if c not in categories]
    if added:
        settings["categories"] = categories + added
    ops.append({"op": "plan.update", "fields": {"settings": settings}})
    counts = changes.counts or {}
    summary = (f"Обновлено из MS Project: {file_name} — новых {len(changes.new)}, изменено {len(changes.changed)}, "
               f"нет в файле {len(changes.missing)}")
    _apply_ops(plan, user, ops, summary)
    _add_comments(plan, user, changes.comments)
    return {"new": len(changes.new), "changed": len(changes.changed), "missing": len(changes.missing),
            "deleted": len(changes.missing) if options["deleteMissing"] else 0, "counts": counts}


def check_revision(plan, expected):
    if expected is not None and int(expected) != plan.revision:
        raise ApiError(409, "plan_changed", "План изменился после проверки — проверьте файл ещё раз")
