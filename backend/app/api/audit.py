"""Реестр изменений: фильтры, постраничный вывод, выгрузка CSV."""

import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, jsonify, request
from sqlalchemy import distinct, func, or_, select

from app.extensions import db
from app.models import AuditLog
from app.security import admin_required
from app.serializers import iso

bp = Blueprint("audit", __name__, url_prefix="/api/audit")

ACTION_LABELS = {
    "create": "создание", "update": "изменение", "delete": "удаление", "restore": "восстановление",
    "purge": "удаление навсегда", "archive": "в архив", "unarchive": "из архива", "snapshot": "версия",
}


def _filtered():
    stmt = select(AuditLog)
    args = request.args
    if args.get("user"):
        stmt = stmt.where(AuditLog.user_name == args["user"])
    if args.get("section"):
        stmt = stmt.where(AuditLog.section == args["section"])
    if args.get("projectId", type=int):
        stmt = stmt.where(AuditLog.project_id == args.get("projectId", type=int))
    elif args.get("project"):
        stmt = stmt.where(AuditLog.project_title == args["project"])
    if args.get("action"):
        stmt = stmt.where(AuditLog.action == args["action"])
    if args.get("from"):
        stmt = stmt.where(AuditLog.created_at >= datetime.combine(date.fromisoformat(args["from"]), datetime.min.time()))
    if args.get("to"):
        stmt = stmt.where(AuditLog.created_at < datetime.combine(date.fromisoformat(args["to"]) + timedelta(days=1),
                                                                datetime.min.time()))
    if args.get("q"):
        like = f"%{args['q'].strip()}%"
        stmt = stmt.where(or_(AuditLog.entity.ilike(like), AuditLog.new_value.ilike(like),
                              AuditLog.old_value.ilike(like), AuditLog.project_title.ilike(like)))
    return stmt


def _row(r):
    return {
        "id": r.id, "createdAt": iso(r.created_at), "userId": r.user_id, "userName": r.user_name,
        "projectId": r.project_id, "projectTitle": r.project_title, "section": r.section, "action": r.action,
        "actionLabel": ACTION_LABELS.get(r.action, r.action), "entity": r.entity, "field": r.field,
        "oldValue": r.old_value, "newValue": r.new_value,
    }


@bp.get("")
@admin_required
def list_audit():
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    stmt = _filtered()
    before = request.args.get("before", type=int)
    if before:
        stmt = stmt.where(AuditLog.id < before)
    rows = db.session.execute(stmt.order_by(AuditLog.id.desc()).limit(limit + 1)).scalars().all()
    total = db.session.execute(select(func.count()).select_from(_filtered().subquery())).scalar_one()
    return jsonify({"rows": [_row(r) for r in rows[:limit]], "total": total, "hasMore": len(rows) > limit,
                    "nextBefore": rows[limit - 1].id if len(rows) > limit else None})


@bp.get("/facets")
@admin_required
def facets():
    def values(column):
        return [v for v in db.session.execute(select(distinct(column)).order_by(column)).scalars() if v]

    return jsonify({"users": values(AuditLog.user_name), "sections": values(AuditLog.section),
                    "projects": values(AuditLog.project_title), "actions": values(AuditLog.action)})


@bp.get("/export.csv")
@admin_required
def export_csv():
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Дата/время (UTC)", "Автор", "Проект", "Раздел", "Действие", "Объект", "Поле", "Было", "Стало"])
    for r in db.session.execute(_filtered().order_by(AuditLog.id.desc()).limit(100000)).scalars():
        writer.writerow([iso(r.created_at), r.user_name, r.project_title, r.section,
                         ACTION_LABELS.get(r.action, r.action), r.entity, r.field, r.old_value, r.new_value])
    data = "﻿" + buf.getvalue()
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=audit.csv"})
