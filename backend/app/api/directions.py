"""Направления — группы проектов на главной странице."""

from typing import Optional

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app import audit
from app.extensions import db
from app.models import Direction, Project, utcnow
from app.security import ApiError, accessible_projects_query, admin_required, current_user, login_required, roles_required
from app.serializers import direction as serialize

bp = Blueprint("directions", __name__, url_prefix="/api/directions")


class DirectionIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    color: Optional[str] = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def _strip(cls, v):
        if v is None:
            return v
        v = " ".join(v.split())
        if not v:
            raise ValueError("Укажите название")
        return v


class ReorderIn(BaseModel):
    ids: list[int]


def _get(direction_id):
    direction = db.session.get(Direction, direction_id)
    if direction is None:
        raise ApiError(404, "not_found", "Направление не найдено")
    return direction


@bp.get("")
@login_required
def list_directions():
    """Администратор и менеджер видят все направления (менеджер выбирает направление для своих проектов);
    остальные — только направления, где есть хотя бы один доступный им проект."""
    user = current_user()
    query = select(Direction).order_by(Direction.position, Direction.id)
    if user.role not in ("admin", "manager"):
        visible = accessible_projects_query(user).with_only_columns(Project.direction_id).where(
            Project.direction_id.is_not(None))
        query = query.where(Direction.id.in_(visible))
    rows = db.session.execute(query).scalars().all()
    return jsonify([serialize(d) for d in rows])


@bp.post("")
@roles_required("admin", "manager")
def create_direction():
    payload = DirectionIn.model_validate(request.get_json(silent=True) or {})
    if not payload.name:
        raise ApiError(400, "validation_error", "Укажите название")
    last = db.session.execute(select(func.max(Direction.position))).scalar() or 0
    direction = Direction(name=payload.name, description=payload.description or "",
                          color=payload.color or "blue", position=last + 1)
    db.session.add(direction)
    db.session.flush()
    audit.record("Направления", "create", f"direction #{direction.id}", "name", "", direction.name)
    db.session.commit()
    return jsonify(serialize(direction)), 201


@bp.patch("/<int:direction_id>")
@roles_required("admin", "manager")
def update_direction(direction_id):
    direction = _get(direction_id)
    payload = DirectionIn.model_validate(request.get_json(silent=True) or {})
    for field in ("name", "description", "color"):
        value = getattr(payload, field)
        if value is not None and getattr(direction, field) != value:
            audit.record("Направления", "update", f"direction #{direction.id}", field, getattr(direction, field), value)
            setattr(direction, field, value)
    direction.updated_at = utcnow()
    db.session.commit()
    return jsonify(serialize(direction))


@bp.post("/reorder")
@admin_required
def reorder():
    payload = ReorderIn.model_validate(request.get_json(silent=True) or {})
    directions = {d.id: d for d in db.session.execute(select(Direction)).scalars()}
    for index, direction_id in enumerate(payload.ids):
        if direction_id in directions:
            directions[direction_id].position = index + 1
    db.session.commit()
    return jsonify({"ok": True})


@bp.delete("/<int:direction_id>")
@admin_required
def delete_direction(direction_id):
    direction = _get(direction_id)
    moved = db.session.execute(
        select(func.count()).select_from(Project).where(Project.direction_id == direction.id)
    ).scalar_one()
    audit.record("Направления", "delete", f"direction #{direction.id}", "name", direction.name, "",
                 details={"projectsMovedToNoDirection": moved})
    db.session.delete(direction)
    db.session.commit()
    return jsonify({"ok": True, "projectsUnassigned": moved})
