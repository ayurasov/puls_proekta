"""Производственный календарь: чтение всем, правка — администратору."""

from datetime import date
from typing import Literal

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import CalendarDay
from app.security import ApiError, admin_required, login_required

bp = Blueprint("calendar", __name__, url_prefix="/api/calendar")


class DayIn(BaseModel):
    kind: Literal["holiday", "workday"]
    title: str = Field(default="", max_length=255)


def _day(d):
    return {"date": d.day.isoformat(), "kind": d.kind, "title": d.title, "source": d.source}


def _parse(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "validation_error", "Неверная дата") from None


@bp.get("")
@login_required
def list_days():
    stmt = select(CalendarDay).order_by(CalendarDay.day)
    if request.args.get("from"):
        stmt = stmt.where(CalendarDay.day >= _parse(request.args["from"]))
    if request.args.get("to"):
        stmt = stmt.where(CalendarDay.day <= _parse(request.args["to"]))
    return jsonify([_day(d) for d in db.session.execute(stmt).scalars()])


@bp.put("/<day>")
@admin_required
def put_day(day):
    parsed = _parse(day)
    payload = DayIn.model_validate(request.get_json(silent=True) or {})
    row = db.session.get(CalendarDay, parsed)
    old = f"{row.kind}: {row.title}" if row else ""
    if row is None:
        row = CalendarDay(day=parsed, kind=payload.kind, title=payload.title, source="custom")
        db.session.add(row)
    else:
        row.kind, row.title, row.source = payload.kind, payload.title, "custom"
    audit.record("Настройки", "update", f"Календарь {parsed.isoformat()}", "kind", old, f"{payload.kind}: {payload.title}")
    db.session.commit()
    return jsonify(_day(row))


@bp.delete("/<day>")
@admin_required
def delete_day(day):
    parsed = _parse(day)
    row = db.session.get(CalendarDay, parsed)
    if row is not None:
        audit.record("Настройки", "delete", f"Календарь {parsed.isoformat()}", "kind", f"{row.kind}: {row.title}", "")
        db.session.delete(row)
        db.session.commit()
    return jsonify({"ok": True})
