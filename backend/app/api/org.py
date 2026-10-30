"""Структура компании (ADR-0008): подразделения, руководители, карточка сотрудника.

Смотрят структуру все сотрудники, меняет — администратор. Форматы обмена (импорт и экспорт) — в
app/api/org_transfer.py.
"""

import re
from typing import Optional

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import Department, User, utcnow
from app.security import ApiError, admin_required, current_user, login_required
from app.services import org
from app.services.org import OrgSnapshot

bp = Blueprint("org", __name__, url_prefix="/api")

KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _dep_or_404(dep_id):
    dep = db.session.get(Department, dep_id)
    if dep is None:
        raise ApiError(404, "not_found", "Подразделение не найдено")
    return dep


def _clean(value):
    return " ".join((value or "").replace("\xa0", " ").split())


# ---------- просмотр ----------

@bp.get("/org/tree")
@login_required
def tree():
    """Вся структура: подразделения со счётчиками и сотрудники с вычисленным руководителем."""
    user = current_user()
    snap = OrgSnapshot(include_inactive=user.role == "admin")
    counts = org.employee_counts(snap)
    return jsonify({
        "settings": org.get_settings(),
        "departments": [org.department_json(d, snap, counts) for d in snap.ordered()],
        "people": [org.person_json(u, snap) for u in sorted(snap.users.values(), key=lambda u: u.name.casefold())],
    })


@bp.get("/org/users/<int:user_id>")
@login_required
def person(user_id):
    """Карточка сотрудника в структуре: подразделение, руководитель, цепочка подчинения, подчинённые."""
    snap = OrgSnapshot(include_inactive=current_user().role == "admin")
    user = snap.users.get(user_id)
    if user is None:
        raise ApiError(404, "not_found", "Сотрудник не найден")
    reports = [u.id for u in snap.users.values() if u.id != user.id and u.is_active and snap.direct_manager(u)[0] == user.id]
    manager_id, source = snap.direct_manager(user)
    return jsonify({
        **org.person_json(user, snap),
        "departmentPath": snap.path(user.department_id) if user.department_id else [],
        "structureManagerId": snap.structure_manager(user),
        "chain": snap.chain(user), "directReports": sorted(reports, key=lambda i: snap.users[i].name.casefold()),
        "directManagerId": manager_id, "managerSource": source,
    })


# ---------- настройки структуры ----------

class UnitKind(BaseModel):
    value: str = Field(max_length=32)
    label: str = Field(min_length=1, max_length=60)
    color: str = "#4f7cff"

    @field_validator("value")
    @classmethod
    def _value(cls, v):
        if not KIND_RE.match(v):
            raise ValueError("Код типа — латиница, цифры, «-» и «_», начинается с буквы")
        return v

    @field_validator("color")
    @classmethod
    def _color(cls, v):
        if not COLOR_RE.match(v):
            raise ValueError("Цвет — в виде #RRGGBB")
        return v


class OrgSettingsIn(BaseModel):
    unitKinds: list[UnitKind] = Field(min_length=1, max_length=30)
    maxDepth: int = Field(default=org.DEFAULT_MAX_DEPTH, ge=2, le=30)


@bp.get("/org/settings")
@login_required
def get_settings():
    return jsonify(org.get_settings())


@bp.put("/org/settings")
@admin_required
def put_settings():
    payload = OrgSettingsIn.model_validate(request.get_json(silent=True) or {})
    kinds = [k.model_dump() for k in payload.unitKinds]
    values = [k["value"] for k in kinds]
    if len(set(values)) != len(values):
        raise ApiError(400, "validation_error", "Коды типов подразделений повторяются")
    used = set(db.session.execute(select(Department.kind).distinct()).scalars())
    missing = used - set(values)
    if missing:
        raise ApiError(409, "kind_in_use", "Нельзя убрать тип, который используется: " + ", ".join(sorted(missing)))
    snap = OrgSnapshot()
    deepest = max((snap.depth(d) for d in snap.departments), default=0)
    if deepest > payload.maxDepth:
        raise ApiError(409, "org_too_deep", f"В структуре уже есть {deepest} уровней — глубину меньше задать нельзя")
    org.save_settings({"unitKinds": kinds, "maxDepth": payload.maxDepth})
    audit.record("Структура", "update", "Настройки структуры", "", "", f"типов: {len(kinds)}, уровней: {payload.maxDepth}")
    db.session.commit()
    return jsonify(org.get_settings())


# ---------- подразделения ----------

class DepartmentIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    parentId: Optional[str] = Field(default=None, max_length=36)
    kind: Optional[str] = Field(default=None, max_length=32)
    sortOrder: Optional[int] = Field(default=None, ge=-100000, le=100000)


def _check_placement(snap, dep_id, name, parent_id):
    if parent_id is not None and parent_id not in snap.departments:
        raise ApiError(400, "validation_error", "Вышестоящее подразделение не найдено")
    if dep_id is not None and parent_id is not None and parent_id in snap.subtree(dep_id):
        raise ApiError(409, "org_cycle", "Нельзя перенести подразделение внутрь самого себя")
    same = snap.find_child(parent_id, name)
    if same is not None and same.id != dep_id:
        raise ApiError(409, "conflict", "Здесь уже есть подразделение с таким названием")
    max_depth = org.get_settings()["maxDepth"]
    own_depth = max((snap.depth(d) for d in snap.subtree(dep_id)), default=0) - snap.depth(dep_id) + 1 \
        if dep_id else 1
    if (snap.depth(parent_id) if parent_id else 0) + own_depth > max_depth:
        raise ApiError(409, "org_too_deep", f"В структуре не больше {max_depth} уровней")


@bp.post("/departments")
@admin_required
def create_department():
    payload = DepartmentIn.model_validate(request.get_json(silent=True) or {})
    name = _clean(payload.name)
    if not name:
        raise ApiError(400, "validation_error", "Укажите название подразделения")
    kind = payload.kind or "department"
    if kind not in org.kind_values():
        raise ApiError(400, "validation_error", "Неизвестный тип подразделения")
    snap = OrgSnapshot()
    _check_placement(snap, None, name, payload.parentId or None)
    siblings = snap.children.get(payload.parentId or None, [])
    sort_order = payload.sortOrder if payload.sortOrder is not None else \
        (max((d.sort_order or 0 for d in siblings), default=-1) + 1)
    dep = Department(id=org.new_id(), name=name, parent_id=payload.parentId or None, kind=kind, sort_order=sort_order,
                     org_source="manual")
    db.session.add(dep)
    audit.record("Структура", "create", name, "", "", " / ".join(snap.path(dep.parent_id) + [name]))
    db.session.commit()
    snap = OrgSnapshot()
    return jsonify(org.department_json(snap.departments[dep.id], snap)), 201


@bp.patch("/departments/<dep_id>")
@admin_required
def update_department(dep_id):
    dep = _dep_or_404(dep_id)
    data = request.get_json(silent=True) or {}
    payload = DepartmentIn.model_validate(data)
    snap = OrgSnapshot()
    name = _clean(payload.name) if payload.name is not None else dep.name
    if not name:
        raise ApiError(400, "validation_error", "Укажите название подразделения")
    parent_id = (payload.parentId or None) if "parentId" in data else dep.parent_id
    _check_placement(snap, dep.id, name, parent_id)
    if payload.kind is not None and payload.kind not in org.kind_values():
        raise ApiError(400, "validation_error", "Неизвестный тип подразделения")
    changes = []
    if name != dep.name:
        changes.append(("name", dep.name, name))
        dep.name = name
    if parent_id != dep.parent_id:
        changes.append(("parentId", " / ".join(snap.path(dep.parent_id)), " / ".join(snap.path(parent_id))))
        dep.parent_id = parent_id
    if payload.kind is not None and payload.kind != dep.kind:
        changes.append(("kind", dep.kind, payload.kind))
        dep.kind = payload.kind
    if payload.sortOrder is not None:
        dep.sort_order = payload.sortOrder
    for field, old, new in changes:
        audit.record("Структура", "update", dep.name, field, old, new)
    dep.updated_at = utcnow()
    db.session.commit()
    snap = OrgSnapshot()
    return jsonify(org.department_json(snap.departments[dep.id], snap))


@bp.delete("/departments/<dep_id>")
@admin_required
def delete_department(dep_id):
    dep = _dep_or_404(dep_id)
    if db.session.execute(select(Department.id).where(Department.parent_id == dep.id)).first():
        raise ApiError(409, "department_has_children", "Сначала перенесите или удалите вложенные подразделения")
    snap = OrgSnapshot()
    path = " / ".join(snap.path(dep.id))
    # Сотрудники остаются в системе без подразделения; руководство подразделением снимается
    db.session.query(User).filter(User.department_id == dep.id).update({User.department_id: None})
    db.session.delete(dep)
    audit.record("Структура", "delete", dep.name, "", path, "")
    db.session.commit()
    return jsonify({"ok": True})


class HeadsIn(BaseModel):
    userIds: list[int] = Field(default_factory=list, max_length=50)
    primaryId: Optional[int] = None


@bp.put("/departments/<dep_id>/heads")
@admin_required
def put_heads(dep_id):
    dep = _dep_or_404(dep_id)
    payload = HeadsIn.model_validate(request.get_json(silent=True) or {})
    ids = list(dict.fromkeys(payload.userIds))
    found = set(db.session.execute(select(User.id).where(User.id.in_(ids))).scalars()) if ids else set()
    if set(ids) - found:
        raise ApiError(400, "validation_error", "Сотрудник не найден")
    primary = payload.primaryId if payload.primaryId in ids else (ids[0] if len(ids) == 1 else None)
    if payload.primaryId is not None and payload.primaryId not in ids:
        raise ApiError(400, "validation_error", "Основной руководитель должен быть среди руководителей")
    snap = OrgSnapshot()
    before = [snap.users[i].name for i, _p in snap.heads.get(dep.id, []) if i in snap.users]
    org.set_heads(dep.id, ids, primary)
    after = [snap.users[i].name for i in ids]
    if before != after:
        audit.record("Структура", "update", dep.name, "heads", ", ".join(before), ", ".join(after))
    db.session.commit()
    snap = OrgSnapshot()
    return jsonify(org.department_json(snap.departments[dep.id], snap))
