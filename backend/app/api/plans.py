"""Планы проекта: документ, операции, синхронизация, комментарии, журнал, снимки."""

from datetime import timedelta
from typing import Optional

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app import audit
from app.extensions import db
from app.models import (
    ItemComment, Plan, PlanAssignee, PlanEvent, PlanItem, PlanNote, PlanPresence, PlanSnapshot, Project, User, utcnow,
)
from app.security import ApiError, LEVEL_ORDER, current_user, login_required, require_project
from app.serializers import iso, user_public
from app.services.legacy_format import plan_to_legacy_state
from app.services.plan_doc import (
    create_snapshot, diff_ops_to_document, plan_document, plan_meta, snapshot_document,
)
from app.services.plan_ops import apply_ops, lock_plan
from app.services.plan_schema import assignee_to_api, item_to_api, note_to_api
from app.services.plans import create_plan, duplicate_plan, import_plan, unique_plan_title

bp = Blueprint("plans", __name__, url_prefix="/api")


class PlanCreate(BaseModel):
    title: str = Field(default="", max_length=500)
    sourcePlanId: Optional[int] = None
    document: Optional[dict] = None


class OpsIn(BaseModel):
    baseRevision: Optional[int] = None
    ops: list[dict]


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    replyToId: Optional[int] = None


class SnapshotIn(BaseModel):
    label: str = Field(default="", max_length=500)


class ReorderIn(BaseModel):
    ids: list[int]


def _plan_with_access(plan_id, minimum="view", allow_archived=True):
    plan = db.session.get(Plan, plan_id)
    if plan is None or plan.deleted_at is not None:
        raise ApiError(404, "not_found", "План не найден")
    project, level = require_project(plan.project_id, minimum)
    if not allow_archived and plan.is_archived:
        raise ApiError(409, "archived", "План в архиве — сначала верните его из архива")
    return plan, project, level


def _audit_plan(project, plan, action, old="", new="", field=""):
    audit.record("Гант", action, f"plan #{plan.id} ({plan.title})", field, old, new, project=project)


@bp.post("/projects/<int:project_id>/plans")
@login_required
def create_plan_route(project_id):
    project, _ = require_project(project_id, "edit")
    user = current_user()
    payload = PlanCreate.model_validate(request.get_json(silent=True) or {})
    if payload.document is not None:
        plan = import_plan(project, user, payload.document, payload.title or None)
        _audit_plan(project, plan, "create", new="импорт из JSON")
    elif payload.sourcePlanId is not None:
        source = db.session.get(Plan, payload.sourcePlanId)
        if source is None or source.project_id != project.id or source.deleted_at is not None:
            raise ApiError(404, "not_found", "Исходный план не найден")
        title = unique_plan_title(project, (payload.title or f"{source.title} (копия)").strip())
        plan = duplicate_plan(project, user, source, title)
        _audit_plan(project, plan, "create", new=f"копия «{source.title}»")
    else:
        title = unique_plan_title(project, (payload.title or "Новый план").strip() or "Новый план")
        plan = create_plan(project, user, title)
        db.session.add(PlanEvent(plan_id=plan.id, revision=0, entity="plan", action="create", user_id=user.id,
                                 user_name=user.name, summary="План создан"))
        _audit_plan(project, plan, "create", new=title)
    project.updated_at = utcnow()
    db.session.commit()
    return jsonify(plan_meta(plan)), 201


@bp.get("/plans/<int:plan_id>")
@login_required
def get_plan(plan_id):
    plan, _project, level = _plan_with_access(plan_id)
    return jsonify(plan_document(plan, level))


@bp.patch("/plans/<int:plan_id>")
@login_required
def update_plan(plan_id):
    """Название, подзаголовок, настройки — то же, что операция plan.update."""
    _plan_with_access(plan_id, "edit", allow_archived=False)
    plan = lock_plan(plan_id)
    fields = {k: v for k, v in (request.get_json(silent=True) or {}).items() if k in ("title", "subtitle", "settings")}
    result = apply_ops(plan, current_user(), [{"op": "plan.update", "fields": fields}], snapshot=False)
    db.session.commit()
    return jsonify({**plan_meta(plan), "revision": result.revision})


@bp.post("/projects/<int:project_id>/plans/reorder")
@login_required
def reorder_plans(project_id):
    project, _ = require_project(project_id, "edit")
    payload = ReorderIn.model_validate(request.get_json(silent=True) or {})
    plans = {p.id: p for p in db.session.execute(select(Plan).where(Plan.project_id == project.id)).scalars()}
    for index, plan_id in enumerate(payload.ids):
        if plan_id in plans:
            plans[plan_id].position = index + 1
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/plans/<int:plan_id>/archive")
@login_required
def archive_plan(plan_id):
    plan, project, _ = _plan_with_access(plan_id, "edit")
    if plan.is_archived:
        return jsonify(plan_meta(plan))
    open_count = db.session.execute(select(func.count()).select_from(Plan).where(
        Plan.project_id == project.id, Plan.deleted_at.is_(None), Plan.is_archived.is_(False))).scalar_one()
    if open_count <= 1:
        raise ApiError(400, "last_plan", "Нельзя убрать в архив единственный открытый план")
    plan.is_archived = True
    plan.archived_at = utcnow()
    plan.archived_by_id = current_user().id
    plan.revision += 1
    plan.meta_rev = plan.revision
    db.session.add(PlanEvent(plan_id=plan.id, revision=plan.revision, entity="plan", action="archive",
                             user_id=current_user().id, user_name=current_user().name, summary="План убран в архив"))
    _audit_plan(project, plan, "archive", new="в архив")
    db.session.commit()
    return jsonify(plan_meta(plan))


@bp.post("/plans/<int:plan_id>/unarchive")
@login_required
def unarchive_plan(plan_id):
    plan, project, _ = _plan_with_access(plan_id, "edit")
    if not plan.is_archived:
        return jsonify(plan_meta(plan))
    plan.is_archived = False
    plan.archived_at = None
    plan.revision += 1
    plan.meta_rev = plan.revision
    db.session.add(PlanEvent(plan_id=plan.id, revision=plan.revision, entity="plan", action="unarchive",
                             user_id=current_user().id, user_name=current_user().name, summary="План возвращён из архива"))
    _audit_plan(project, plan, "unarchive", new="из архива")
    db.session.commit()
    return jsonify(plan_meta(plan))


@bp.delete("/plans/<int:plan_id>")
@login_required
def delete_plan(plan_id):
    """В корзину; удалить можно только архивный план, право — «полное управление»."""
    plan, project, _ = _plan_with_access(plan_id, "manage")
    if not plan.is_archived:
        raise ApiError(409, "not_archived", "Сначала уберите план в архив")
    plan.deleted_at = utcnow()
    plan.deleted_by_id = current_user().id
    _audit_plan(project, plan, "delete", old=plan.title, new="в корзину")
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/plans/<int:plan_id>/restore")
@login_required
def restore_plan(plan_id):
    plan = db.session.get(Plan, plan_id)
    if plan is None or plan.deleted_at is None:
        raise ApiError(404, "not_found", "Плана нет в корзине")
    project, _ = require_project(plan.project_id, "manage")
    plan.deleted_at = None
    plan.deleted_by_id = None
    _audit_plan(project, plan, "restore", old="корзина", new=plan.title)
    db.session.commit()
    return jsonify(plan_meta(plan))


@bp.delete("/trash/plans/<int:plan_id>")
@login_required
def purge_plan(plan_id):
    plan = db.session.get(Plan, plan_id)
    if plan is None or plan.deleted_at is None:
        raise ApiError(404, "not_found", "Плана нет в корзине")
    if current_user().role != "admin":
        raise ApiError(403, "forbidden", "Удалить навсегда может только администратор")
    project = db.session.get(Project, plan.project_id)
    _audit_plan(project, plan, "purge", old=plan.title, new="удалён навсегда")
    db.session.delete(plan)
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/plans/<int:plan_id>/ops")
@login_required
def plan_ops(plan_id):
    _plan, project, _level = _plan_with_access(plan_id, "edit", allow_archived=False)
    payload = OpsIn.model_validate(request.get_json(silent=True) or {})
    plan = lock_plan(plan_id)
    result = apply_ops(plan, current_user(), payload.ops, payload.baseRevision, project=project)
    _touch_presence(plan.id, current_user().id, editing=True)
    db.session.commit()
    data = result.as_dict()
    if result.plan_changed:
        data["plan"] = plan_meta(plan)
    if result.conflicts:
        names = {u.id: u.name for u in db.session.execute(
            select(User).where(User.id.in_({c["byId"] for c in result.conflicts}))).scalars()}
        for conflict in result.conflicts:
            conflict["byName"] = names.get(conflict["byId"], "")
    return jsonify(data)


def _touch_presence(plan_id, user_id, editing=False):
    row = db.session.get(PlanPresence, (plan_id, user_id))
    if row is None:
        db.session.add(PlanPresence(plan_id=plan_id, user_id=user_id, last_seen_at=utcnow(), is_editing=editing))
    else:
        row.last_seen_at = utcnow()
        row.is_editing = editing


def _presence(plan_id, me_id):
    ttl = timedelta(seconds=current_app.config.get("PRESENCE_TTL_SECONDS", 45))
    rows = db.session.execute(
        select(PlanPresence, User).join(User, User.id == PlanPresence.user_id)
        .where(PlanPresence.plan_id == plan_id, PlanPresence.last_seen_at >= utcnow() - ttl, User.id != me_id)
    ).all()
    return [{**user_public(u), "editing": bool(p.is_editing)} for p, u in rows]


@bp.get("/plans/<int:plan_id>/changes")
@login_required
def plan_changes(plan_id):
    """Изменения коллег после ревизии `since` и кто сейчас на плане (опрос раз в несколько секунд)."""
    plan, _project, level = _plan_with_access(plan_id)
    since = request.args.get("since", type=int)
    editing = request.args.get("editing") == "1" and LEVEL_ORDER[level] >= LEVEL_ORDER["edit"]
    user = current_user()
    _touch_presence(plan.id, user.id, editing)
    db.session.commit()
    data = {"revision": plan.revision, "access": level, "presence": _presence(plan.id, user.id),
            "isArchived": bool(plan.is_archived)}
    if since is None or since >= plan.revision:
        data["unchanged"] = True
        return jsonify(data)

    def changed(model):
        return db.session.execute(select(model).where(model.plan_id == plan.id, model.updated_rev > since)).scalars().all()

    data["items"] = [item_to_api(i) for i in changed(PlanItem)]
    data["notes"] = [note_to_api(n) for n in changed(PlanNote)]
    data["assignees"] = [assignee_to_api(a) for a in changed(PlanAssignee)]
    if plan.meta_rev > since:
        data["plan"] = plan_meta(plan, level)
    events = db.session.execute(
        select(PlanEvent).where(PlanEvent.plan_id == plan.id, PlanEvent.revision > since, PlanEvent.user_id != user.id)
        .order_by(PlanEvent.id.desc()).limit(20)
    ).scalars().all()
    data["events"] = [_event(e) for e in events]
    return jsonify(data)


@bp.delete("/plans/<int:plan_id>/presence")
@login_required
def leave_plan(plan_id):
    db.session.execute(delete(PlanPresence).where(PlanPresence.plan_id == plan_id,
                                                  PlanPresence.user_id == current_user().id))
    db.session.commit()
    return jsonify({"ok": True})


# ---------- комментарии ----------

def _comment(c, users=None):
    data = {
        "id": c.id, "itemKey": c.item_key, "authorId": c.author_id, "authorName": c.author_name,
        "text": c.text if c.deleted_at is None else "", "replyToId": c.reply_to_id,
        "createdAt": iso(c.created_at), "updatedAt": iso(c.updated_at), "deleted": c.deleted_at is not None,
    }
    if users and c.author_id in users:
        data["authorAvatarUrl"] = users[c.author_id].get("avatarUrl")
    return data


def _bump_comment_count(plan, item_key, user, summary):
    item = db.session.execute(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.key == item_key)) \
        .scalar_one_or_none()
    count = db.session.execute(select(func.count()).select_from(ItemComment).where(
        ItemComment.plan_id == plan.id, ItemComment.item_key == item_key, ItemComment.deleted_at.is_(None))).scalar_one()
    plan.revision += 1
    if item is not None:
        item.comment_count = count
        item.updated_rev = plan.revision
    db.session.add(PlanEvent(plan_id=plan.id, revision=plan.revision, entity="item", entity_key=item_key,
                             action="comment", user_id=user.id, user_name=user.name, summary=summary))


@bp.get("/plans/<int:plan_id>/items/<item_key>/comments")
@login_required
def list_comments(plan_id, item_key):
    plan, _project, _level = _plan_with_access(plan_id)
    comments = db.session.execute(
        select(ItemComment).where(ItemComment.plan_id == plan.id, ItemComment.item_key == item_key)
        .order_by(ItemComment.created_at, ItemComment.id)
    ).scalars().all()
    author_ids = {c.author_id for c in comments if c.author_id}
    users = {u.id: user_public(u) for u in db.session.execute(select(User).where(User.id.in_(author_ids))).scalars()} \
        if author_ids else {}
    return jsonify([_comment(c, users) for c in comments])


@bp.post("/plans/<int:plan_id>/items/<item_key>/comments")
@login_required
def add_comment(plan_id, item_key):
    _plan_with_access(plan_id, "edit", allow_archived=False)
    plan = lock_plan(plan_id)
    user = current_user()
    payload = CommentIn.model_validate(request.get_json(silent=True) or {})
    item = db.session.execute(select(PlanItem).where(
        PlanItem.plan_id == plan.id, PlanItem.key == item_key, PlanItem.deleted_at.is_(None))).scalar_one_or_none()
    if item is None:
        raise ApiError(404, "not_found", "Строка не найдена")
    if payload.replyToId is not None:
        parent = db.session.get(ItemComment, payload.replyToId)
        if parent is None or parent.plan_id != plan.id or parent.item_key != item_key:
            raise ApiError(400, "validation_error", "Комментарий для ответа не найден")
    text = payload.text.strip()
    if not text:
        raise ApiError(400, "validation_error", "Пустой комментарий")
    comment = ItemComment(plan_id=plan.id, item_key=item_key, author_id=user.id, author_name=user.name,
                          text=text, reply_to_id=payload.replyToId)
    db.session.add(comment)
    db.session.flush()
    _bump_comment_count(plan, item_key, user, f"Комментарий к «{item.name}»: {text[:200]}")
    db.session.commit()
    return jsonify({"comment": _comment(comment, {user.id: user_public(user)}), "revision": plan.revision,
                    "commentCount": item.comment_count}), 201


@bp.patch("/comments/<int:comment_id>")
@login_required
def edit_comment(comment_id):
    comment = db.session.get(ItemComment, comment_id)
    if comment is None or comment.deleted_at is not None:
        raise ApiError(404, "not_found", "Комментарий не найден")
    _plan_with_access(comment.plan_id, "edit", allow_archived=False)
    user = current_user()
    if comment.author_id != user.id:
        raise ApiError(403, "forbidden", "Изменить комментарий может только автор")
    payload = CommentIn.model_validate(request.get_json(silent=True) or {})
    comment.text = payload.text.strip()
    comment.updated_at = utcnow()
    db.session.commit()
    return jsonify(_comment(comment))


@bp.delete("/comments/<int:comment_id>")
@login_required
def delete_comment(comment_id):
    comment = db.session.get(ItemComment, comment_id)
    if comment is None or comment.deleted_at is not None:
        raise ApiError(404, "not_found", "Комментарий не найден")
    _plan, _project, level = _plan_with_access(comment.plan_id, "edit", allow_archived=False)
    user = current_user()
    if comment.author_id != user.id and level != "manage":
        raise ApiError(403, "forbidden", "Удалить комментарий может автор или сотрудник с полным управлением")
    plan = lock_plan(comment.plan_id)
    comment.deleted_at = utcnow()
    comment.deleted_by_id = user.id
    _bump_comment_count(plan, comment.item_key, user, f"Удалён комментарий: {comment.text[:200]}")
    db.session.commit()
    return jsonify({"ok": True, "revision": plan.revision})


# ---------- журнал ----------

def _event(e):
    return {
        "id": e.id, "revision": e.revision, "entity": e.entity, "entityKey": e.entity_key, "action": e.action,
        "userId": e.user_id, "userName": e.user_name, "createdAt": iso(e.created_at), "summary": e.summary,
        "changes": e.changes if e.action != "legacy" else None,
    }


@bp.get("/plans/<int:plan_id>/events")
@login_required
def list_events(plan_id):
    plan, _project, _level = _plan_with_access(plan_id)
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    stmt = select(PlanEvent).where(PlanEvent.plan_id == plan.id)
    entity_key = request.args.get("entityKey")
    if entity_key:
        stmt = stmt.where(PlanEvent.entity_key == entity_key)
    before = request.args.get("before", type=int)
    if before:
        stmt = stmt.where(PlanEvent.id < before)
    rows = db.session.execute(stmt.order_by(PlanEvent.created_at.desc(), PlanEvent.id.desc()).limit(limit + 1)) \
        .scalars().all()
    return jsonify({"events": [_event(e) for e in rows[:limit]], "hasMore": len(rows) > limit,
                    "nextBefore": rows[limit - 1].id if len(rows) > limit else None})


# ---------- снимки ----------

def _snapshot(s):
    return {"id": s.id, "revision": s.revision, "reason": s.reason, "label": s.label, "createdAt": iso(s.created_at),
            "userName": s.user_name, "itemsCount": s.items_count}


@bp.get("/plans/<int:plan_id>/snapshots")
@login_required
def list_snapshots(plan_id):
    plan, _project, _level = _plan_with_access(plan_id)
    rows = db.session.execute(select(PlanSnapshot).where(PlanSnapshot.plan_id == plan.id)
                              .order_by(PlanSnapshot.created_at.desc(), PlanSnapshot.id.desc())).scalars().all()
    return jsonify([_snapshot(s) for s in rows])


@bp.post("/plans/<int:plan_id>/snapshots")
@login_required
def make_snapshot(plan_id):
    plan, project, _ = _plan_with_access(plan_id, "edit")
    payload = SnapshotIn.model_validate(request.get_json(silent=True) or {})
    snapshot = create_snapshot(plan, current_user(), "manual", payload.label.strip() or "Сохранённая версия")
    _audit_plan(project, plan, "snapshot", new=snapshot.label)
    db.session.commit()
    return jsonify(_snapshot(snapshot)), 201


@bp.get("/plans/<int:plan_id>/snapshots/<int:snapshot_id>")
@login_required
def get_snapshot(plan_id, snapshot_id):
    plan, _project, _level = _plan_with_access(plan_id)
    snapshot = db.session.get(PlanSnapshot, snapshot_id)
    if snapshot is None or snapshot.plan_id != plan.id:
        raise ApiError(404, "not_found", "Снимок не найден")
    return jsonify({**_snapshot(snapshot), "data": snapshot.data})


@bp.post("/plans/<int:plan_id>/snapshots/<int:snapshot_id>/restore")
@login_required
def restore_snapshot(plan_id, snapshot_id):
    _plan_with_access(plan_id, "edit", allow_archived=False)
    plan = lock_plan(plan_id)
    snapshot = db.session.get(PlanSnapshot, snapshot_id)
    if snapshot is None or snapshot.plan_id != plan.id:
        raise ApiError(404, "not_found", "Снимок не найден")
    user = current_user()
    when = snapshot.created_at
    create_snapshot(plan, user, "before_restore", f"Перед восстановлением снимка от {iso(when)}")
    ops = diff_ops_to_document(plan, snapshot.data)
    result = None
    if ops:
        result = apply_ops(plan, user, ops, snapshot=False, audit_summary=False)
    db.session.add(PlanEvent(plan_id=plan.id, revision=plan.revision, entity="plan", action="restore_snapshot",
                             user_id=user.id, user_name=user.name,
                             summary=f"Восстановлено состояние плана (снимок {snapshot.id}, изменений: {len(ops)})"))
    _audit_plan(plan.project, plan, "restore", new=f"снимок {snapshot.id}: {len(ops)} изменений")
    db.session.commit()
    return jsonify({"revision": plan.revision, "changes": len(ops), "fixes": result.fixes if result else []})


# ---------- экспорт ----------

@bp.get("/plans/<int:plan_id>/export")
@login_required
def export_plan(plan_id):
    plan, _project, _level = _plan_with_access(plan_id)
    fmt = request.args.get("format", "legacy")
    if fmt == "snapshot":
        return jsonify(snapshot_document(plan))
    items = db.session.execute(select(PlanItem).where(PlanItem.plan_id == plan.id)).scalars().all()
    notes = db.session.execute(select(PlanNote).where(PlanNote.plan_id == plan.id)).scalars().all()
    assignees = db.session.execute(select(PlanAssignee).where(PlanAssignee.plan_id == plan.id)).scalars().all()
    comments = {}
    for c in db.session.execute(select(ItemComment).where(ItemComment.plan_id == plan.id, ItemComment.deleted_at.is_(None))
                                .order_by(ItemComment.created_at, ItemComment.id)).scalars():
        comments.setdefault(c.item_key, []).append(c)
    events = {}
    for e in db.session.execute(select(PlanEvent).where(PlanEvent.plan_id == plan.id, PlanEvent.entity == "item")
                                .order_by(PlanEvent.created_at, PlanEvent.id)).scalars():
        events.setdefault(e.entity_key, []).append(e)
    state = plan_to_legacy_state(plan, items, notes, assignees, comments, events)
    response = jsonify(state)
    return response
