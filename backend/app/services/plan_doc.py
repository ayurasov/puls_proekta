"""Документ плана: чтение целиком, снимки, восстановление из снимка."""

from datetime import timedelta

from sqlalchemy import delete, select

from app.extensions import db
from app.models import Plan, PlanAssignee, PlanItem, PlanNote, PlanSnapshot, User, utcnow
from app.serializers import iso
from app.services.plan_schema import assignee_to_api, item_to_api, normalize_settings, note_to_api

DOC_FORMAT = "puls-plan"
DOC_VERSION = 1


def plan_meta(plan, level=None):
    data = {
        "id": plan.id,
        "key": plan.key,
        "projectId": plan.project_id,
        "title": plan.title,
        "subtitle": plan.subtitle or "",
        "position": plan.position,
        "isArchived": bool(plan.is_archived),
        "archivedAt": iso(plan.archived_at),
        "revision": plan.revision,
        "settings": normalize_settings(plan.settings),
        "createdAt": iso(plan.created_at),
        "updatedAt": iso(plan.updated_at),
        "updatedById": plan.updated_by_id,
    }
    if level is not None:
        data["access"] = level
    return data


def load_entities(plan, include_deleted=False):
    def q(model, order):
        stmt = select(model).where(model.plan_id == plan.id)
        if not include_deleted:
            stmt = stmt.where(model.deleted_at.is_(None))
        return db.session.execute(stmt.order_by(*order)).scalars().all()

    items = q(PlanItem, (PlanItem.position, PlanItem.id))
    notes = q(PlanNote, (PlanNote.date, PlanNote.id))
    assignees = q(PlanAssignee, (PlanAssignee.position, PlanAssignee.id))
    return items, notes, assignees


def plan_document(plan, level=None):
    items, notes, assignees = load_entities(plan)
    editor_ids = {i.updated_by_id for i in items if i.updated_by_id} | ({plan.updated_by_id} - {None})
    users = {}
    if editor_ids:
        for user in db.session.execute(select(User).where(User.id.in_(editor_ids))).scalars():
            users[str(user.id)] = {"name": user.name}
    return {
        "plan": plan_meta(plan, level),
        "items": [item_to_api(i) for i in items],
        "notes": [note_to_api(n) for n in notes],
        "assignees": [assignee_to_api(a) for a in assignees],
        "users": users,
    }


def _strip_volatile(entity):
    return {k: v for k, v in entity.items() if k not in ("updatedAt", "updatedById", "updatedRev", "commentCount")}


def snapshot_document(plan):
    items, notes, assignees = load_entities(plan)
    return {
        "format": DOC_FORMAT,
        "version": DOC_VERSION,
        "plan": {
            "key": plan.key,
            "title": plan.title,
            "subtitle": plan.subtitle or "",
            "settings": normalize_settings(plan.settings),
            "revision": plan.revision,
        },
        "items": [_strip_volatile(item_to_api(i)) for i in items],
        "notes": [_strip_volatile(note_to_api(n)) for n in notes],
        "assignees": [_strip_volatile(assignee_to_api(a)) for a in assignees],
    }


def create_snapshot(plan, user, reason="auto", label=""):
    doc = snapshot_document(plan)
    snapshot = PlanSnapshot(
        plan_id=plan.id, revision=plan.revision, reason=reason, label=label[:500],
        created_by_id=user.id if user else None, user_name=user.name if user else "система",
        items_count=len(doc["items"]), data=doc,
    )
    db.session.add(snapshot)
    plan.last_snapshot_at = utcnow()
    if reason == "auto":
        prune_snapshots(plan)
    return snapshot


def maybe_auto_snapshot(plan, user, interval_minutes):
    """Снимок состояния до правки, если последний снимок старше интервала."""
    if plan.revision == 0:
        return None
    if plan.last_snapshot_at is not None and utcnow() - plan.last_snapshot_at < timedelta(minutes=interval_minutes):
        return None
    return create_snapshot(plan, user, "auto")


def prune_snapshots(plan):
    """Автоматические снимки: все за 2 суток, по одному в день за 60 дней, дальше — по одному в неделю.
    Ручные снимки и снимки перед восстановлением/импортом не удаляются."""
    now = utcnow()
    rows = db.session.execute(
        select(PlanSnapshot.id, PlanSnapshot.created_at)
        .where(PlanSnapshot.plan_id == plan.id, PlanSnapshot.reason == "auto")
        .order_by(PlanSnapshot.created_at.desc())
    ).all()
    keep_buckets = set()
    drop = []
    for snapshot_id, created_at in rows:
        age = now - created_at
        if age <= timedelta(days=2):
            continue
        if age <= timedelta(days=60):
            bucket = ("d", created_at.date())
        else:
            iso_year, iso_week, _ = created_at.isocalendar()
            bucket = ("w", iso_year, iso_week)
        if bucket in keep_buckets:
            drop.append(snapshot_id)
        else:
            keep_buckets.add(bucket)
    if drop:
        db.session.execute(delete(PlanSnapshot).where(PlanSnapshot.id.in_(drop)))


def diff_ops_to_document(plan, doc):
    """Операции, приводящие текущее состояние плана к документу `doc` (для восстановления снимка)."""
    items, notes, assignees = load_entities(plan, include_deleted=True)
    ops = []

    def sync(kind, current, target, to_api):
        current_by_key = {e.key: e for e in current}
        target_by_key = {e["key"]: e for e in target}
        for key, entity in current_by_key.items():
            if entity.deleted_at is None and key not in target_by_key:
                ops.append({"op": f"{kind}.delete", "key": key})
        for key, wanted in target_by_key.items():
            fields = {k: v for k, v in wanted.items() if k not in ("key", "type", "deleted")}
            existing = current_by_key.get(key)
            if existing is None:
                payload = dict(wanted)
                payload.pop("deleted", None)
                ops.append({"op": f"{kind}.create", kind: payload})
                continue
            if existing.deleted_at is not None:
                ops.append({"op": f"{kind}.restore", "key": key})
            now_api = _strip_volatile(to_api(existing))
            changed = {k: v for k, v in fields.items() if now_api.get(k) != v}
            if kind == "item" and wanted.get("type") != existing.type:
                ops.append({"op": "item.delete", "key": key})
                continue
            if changed:
                ops.append({"op": f"{kind}.update", "key": key, "fields": changed})

    sync("assignee", assignees, doc.get("assignees", []), assignee_to_api)
    sync("item", items, doc.get("items", []), item_to_api)
    sync("note", notes, doc.get("notes", []), note_to_api)
    plan_doc = doc.get("plan") or {}
    fields = {}
    for key in ("title", "subtitle"):
        if key in plan_doc and plan_doc[key] != getattr(plan, key):
            fields[key] = plan_doc[key]
    if "settings" in plan_doc and normalize_settings(plan_doc["settings"]) != normalize_settings(plan.settings):
        fields["settings"] = plan_doc["settings"]
    if fields:
        ops.append({"op": "plan.update", "fields": fields})
    return ops


def get_plan(plan_id):
    return db.session.get(Plan, plan_id)
