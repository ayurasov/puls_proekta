"""Создание планов: пустой, копия существующего, из документа (снимок или JSON 1.0.x)."""

import secrets
import time

from sqlalchemy import func, select

from app.extensions import db
from app.models import ItemComment, Plan, PlanEvent, PlanItem, utcnow
from app.security import ApiError
from app.services.legacy_format import document_from_any
from app.services.plan_doc import snapshot_document
from app.services.plan_ops import PlanEditor
from app.services.plan_schema import default_settings, normalize_settings


def new_plan_key():
    # Формат ключей вкладок 1.0.x: p_<время>_<4 символа>
    return f"p_{int(time.time() * 1000)}_{secrets.token_hex(2)}"


def unique_plan_title(project, title):
    existing = set(db.session.execute(
        select(Plan.title).where(Plan.project_id == project.id, Plan.deleted_at.is_(None))
    ).scalars())
    if title not in existing:
        return title
    n = 2
    while f"{title} ({n})" in existing:
        n += 1
    return f"{title} ({n})"


def _next_position(project):
    last = db.session.execute(
        select(func.max(Plan.position)).where(Plan.project_id == project.id)
    ).scalar()
    return (last or 0) + 1


def create_plan(project, user, title, settings=None, subtitle=""):
    plan = Plan(project_id=project.id, key=new_plan_key(), title=title, subtitle=subtitle or "",
                position=_next_position(project), settings=normalize_settings(settings or default_settings()),
                created_by_id=user.id if user else None, updated_by_id=user.id if user else None)
    db.session.add(plan)
    db.session.flush()
    return plan


def fill_plan_from_document(plan, user, doc, event_summary):
    """Заполнить пустой план содержимым документа одной ревизией."""
    ops = []
    for assignee in doc.get("assignees", []):
        ops.append({"op": "assignee.create", "assignee": assignee})
    for item in doc.get("items", []):
        ops.append({"op": "item.create", "item": item})
    for note in doc.get("notes", []):
        ops.append({"op": "note.create", "note": note})
    editor = PlanEditor(plan, user)
    if ops:
        editor.apply(ops)
    plan.revision = editor.revision
    plan.updated_at = editor.now
    db.session.add(PlanEvent(plan_id=plan.id, revision=plan.revision, entity="plan", action="create",
                             user_id=user.id if user else None, user_name=user.name if user else "система",
                             summary=event_summary))
    return editor.result


def duplicate_plan(project, user, source, title):
    doc = snapshot_document(source)
    plan = create_plan(project, user, title, settings=source.settings, subtitle=source.subtitle)
    plan.next_row_seq = source.next_row_seq
    fill_plan_from_document(plan, user, doc, f"План создан копированием «{source.title}»")
    return plan


def import_plan(project, user, payload, title=None):
    """План из JSON: снимок «Пульса Проекта» или файл экспорта 1.0.x."""
    try:
        doc, comments = document_from_any(payload)
    except ValueError as exc:
        raise ApiError(400, "invalid_import", str(exc)) from None
    plan_doc = doc.get("plan") or {}
    plan_title = unique_plan_title(project, (title or plan_doc.get("title") or "Импорт").strip()[:500] or "Импорт")
    plan = create_plan(project, user, plan_title, settings=plan_doc.get("settings"), subtitle=plan_doc.get("subtitle", ""))
    if plan_doc.get("nextRowSeq"):
        plan.next_row_seq = int(plan_doc["nextRowSeq"])
    fill_plan_from_document(plan, user, doc, f"План импортирован из файла ({len(doc.get('items', []))} строк)")
    add_imported_comments(plan, comments)
    return plan


def add_imported_comments(plan, comments):
    """Комментарии строк из JSON 1.0.x (ответы ссылаются на номер комментария строки)."""
    by_item = {}
    for comment in comments:
        by_item.setdefault(comment["itemKey"], []).append(comment)
    items = {i.key: i for i in db.session.execute(select(PlanItem).where(PlanItem.plan_id == plan.id)).scalars()}
    for item_key, entries in by_item.items():
        item = items.get(item_key)
        if item is None:
            continue
        created = []
        for entry in entries:
            row = ItemComment(plan_id=plan.id, item_key=item_key, author_id=entry.get("authorId"),
                              author_name=entry.get("authorName") or "", text=entry.get("text") or "",
                              created_at=entry.get("createdAt") or utcnow(), extra=entry.get("extra"))
            db.session.add(row)
            created.append((entry, row))
        db.session.flush()
        for entry, row in created:
            reply = entry.get("replyToIndex")
            if isinstance(reply, int) and 0 <= reply < len(created):
                row.reply_to_id = created[reply][1].id
        item.comment_count = len(created)
