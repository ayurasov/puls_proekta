"""Правила доступа к проектам и направлениям (администратор)."""

from typing import Literal

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import AccessRule, Department, DepartmentAccessRule, Direction, Project, User
from app.security import ApiError, admin_required, best_level, cap_level, department_chain
from app.serializers import iso

bp = Blueprint("access", __name__, url_prefix="/api/access")

LEVEL_LABELS = {"view": "Просмотр", "edit": "Редактирование", "manage": "Полное управление"}
Level = Literal["view", "edit", "manage"]


class BulkGrant(BaseModel):
    userIds: list[int] = Field(min_length=1, max_length=5000)
    projectIds: list[int] = Field(default_factory=list, max_length=5000)
    directionIds: list[int] = Field(default_factory=list, max_length=500)
    level: Level = "view"
    # Если у сотрудника уже есть правило: replace — заменить уровень, raise — только повысить
    mode: Literal["replace", "raise"] = "replace"


class RuleUpdate(BaseModel):
    level: Level


class BulkDelete(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=10000)


def _serialize(rule, users, projects, directions):
    user = users.get(rule.user_id)
    data = {
        "id": rule.id,
        "userId": rule.user_id,
        "userName": user.name if user else f"#{rule.user_id}",
        "userLogin": user.login if user else "",
        "userRole": user.role if user else "",
        "projectId": rule.project_id,
        "directionId": rule.direction_id,
        "targetName": (projects[rule.project_id].title if rule.project_id in projects
                       else directions[rule.direction_id].name if rule.direction_id in directions else ""),
        "level": rule.level,
        "effectiveLevel": cap_level(user, rule.level) if user else rule.level,
        "createdAt": iso(rule.created_at),
    }
    return data


def _lookups():
    users = {u.id: u for u in db.session.execute(select(User)).scalars()}
    projects = {p.id: p for p in db.session.execute(select(Project)).scalars()}
    directions = {d.id: d for d in db.session.execute(select(Direction)).scalars()}
    return users, projects, directions


@bp.get("")
@admin_required
def list_rules():
    stmt = select(AccessRule).order_by(AccessRule.id)
    if request.args.get("userId", type=int):
        stmt = stmt.where(AccessRule.user_id == request.args.get("userId", type=int))
    if request.args.get("projectId", type=int):
        stmt = stmt.where(AccessRule.project_id == request.args.get("projectId", type=int))
    rules = db.session.execute(stmt).scalars().all()
    users, projects, directions = _lookups()
    return jsonify([_serialize(r, users, projects, directions) for r in rules])


@bp.post("")
@admin_required
def grant():
    """Массовая выдача: каждому из сотрудников — к каждому проекту/направлению. Повторов не создаёт."""
    payload = BulkGrant.model_validate(request.get_json(silent=True) or {})
    # Повторы в списках не создают второе правило (уникальный индекс) — убираем их
    payload.userIds = list(dict.fromkeys(payload.userIds))
    payload.projectIds = list(dict.fromkeys(payload.projectIds))
    payload.directionIds = list(dict.fromkeys(payload.directionIds))
    if not payload.projectIds and not payload.directionIds:
        raise ApiError(400, "validation_error", "Выберите проекты или направления")
    users, projects, directions = _lookups()
    for uid in payload.userIds:
        if uid not in users:
            raise ApiError(400, "validation_error", f"Сотрудник #{uid} не найден")
    targets = [("project_id", pid) for pid in payload.projectIds] + [("direction_id", did) for did in payload.directionIds]
    for column, target_id in targets:
        if column == "project_id" and (target_id not in projects or projects[target_id].deleted_at is not None):
            raise ApiError(400, "validation_error", f"Проект #{target_id} не найден")
        if column == "direction_id" and target_id not in directions:
            raise ApiError(400, "validation_error", f"Направление #{target_id} не найдено")
    existing = {}
    for rule in db.session.execute(select(AccessRule).where(AccessRule.user_id.in_(payload.userIds))).scalars():
        existing[(rule.user_id, "project_id" if rule.project_id else "direction_id",
                  rule.project_id or rule.direction_id)] = rule
    created = updated = unchanged = 0
    for uid in payload.userIds:
        for column, target_id in targets:
            target_name = projects[target_id].title if column == "project_id" else directions[target_id].name
            entity = f"user #{uid} ({users[uid].name}) → {target_name}"
            rule = existing.get((uid, column, target_id))
            if rule is None:
                rule = AccessRule(user_id=uid, level=payload.level, **{column: target_id})
                db.session.add(rule)
                created += 1
                audit.record("Доступы", "create", entity, "level", "", LEVEL_LABELS[payload.level],
                             project=projects.get(target_id) if column == "project_id" else None)
                continue
            new_level = payload.level if payload.mode == "replace" else best_level(rule.level, payload.level)
            if new_level == rule.level:
                unchanged += 1
                continue
            audit.record("Доступы", "update", entity, "level", LEVEL_LABELS[rule.level], LEVEL_LABELS[new_level],
                         project=projects.get(target_id) if column == "project_id" else None)
            rule.level = new_level
            updated += 1
    db.session.commit()
    return jsonify({"created": created, "updated": updated, "unchanged": unchanged}), 201


@bp.patch("/<int:rule_id>")
@admin_required
def update_rule(rule_id):
    rule = db.session.get(AccessRule, rule_id)
    if rule is None:
        raise ApiError(404, "not_found", "Правило не найдено")
    payload = RuleUpdate.model_validate(request.get_json(silent=True) or {})
    if payload.level != rule.level:
        users, projects, directions = _lookups()
        target = projects[rule.project_id].title if rule.project_id else directions[rule.direction_id].name
        audit.record("Доступы", "update", f"user #{rule.user_id} ({users[rule.user_id].name}) → {target}", "level",
                     LEVEL_LABELS[rule.level], LEVEL_LABELS[payload.level],
                     project=projects.get(rule.project_id) if rule.project_id else None)
        rule.level = payload.level
        db.session.commit()
    users, projects, directions = _lookups()
    return jsonify(_serialize(rule, users, projects, directions))


@bp.delete("/<int:rule_id>")
@admin_required
def delete_rule(rule_id):
    return _delete([rule_id])


@bp.post("/delete")
@admin_required
def delete_many():
    payload = BulkDelete.model_validate(request.get_json(silent=True) or {})
    return _delete(payload.ids)


def _delete(ids):
    users, projects, directions = _lookups()
    rules = db.session.execute(select(AccessRule).where(AccessRule.id.in_(ids))).scalars().all()
    for rule in rules:
        target = projects[rule.project_id].title if rule.project_id else directions[rule.direction_id].name
        user = users.get(rule.user_id)
        audit.record("Доступы", "delete", f"user #{rule.user_id} ({user.name if user else ''}) → {target}", "level",
                     LEVEL_LABELS[rule.level], "", project=projects.get(rule.project_id) if rule.project_id else None)
        db.session.delete(rule)
    db.session.commit()
    return jsonify({"deleted": len(rules)})


@bp.get("/effective")
@admin_required
def effective():
    """Матрица «сотрудник × проект» с итоговыми уровнями."""
    users = db.session.execute(select(User).where(User.is_active.is_(True)).order_by(User.name)).scalars().all()
    projects = db.session.execute(select(Project).where(Project.deleted_at.is_(None))
                                  .order_by(Project.position, Project.id)).scalars().all()
    rules = db.session.execute(select(AccessRule)).scalars().all()
    by_user = {}
    for rule in rules:
        by_user.setdefault(rule.user_id, []).append(rule)
    by_department = {}
    for rule in db.session.execute(select(DepartmentAccessRule)).scalars():
        by_department.setdefault(rule.department_id, []).append(rule)
    parents = dict(db.session.execute(select(Department.id, Department.parent_id)).all())
    for user in users:
        for dep_id in department_chain(user, parents):
            by_user.setdefault(user.id, []).extend(by_department.get(dep_id, []))
    matrix = {}
    for user in users:
        row = {}
        for project in projects:
            if user.role == "admin":
                row[project.id] = "manage"
                continue
            level = ""
            for rule in by_user.get(user.id, []):
                if rule.project_id == project.id or (rule.direction_id and rule.direction_id == project.direction_id):
                    level = best_level(level, rule.level)
            if user.role == "manager" and project.created_by_id == user.id:
                level = "manage"
            level = cap_level(user, level)
            if level:
                row[project.id] = level
        matrix[user.id] = row
    return jsonify({
        "users": [{"id": u.id, "name": u.name, "role": u.role} for u in users],
        "projects": [{"id": p.id, "title": p.title, "directionId": p.direction_id} for p in projects],
        "matrix": matrix,
    })



# ---------- доступ подразделений (ADR-0008) ----------

class DepartmentGrant(BaseModel):
    departmentIds: list[str] = Field(min_length=1, max_length=1000)
    projectIds: list[int] = Field(default_factory=list, max_length=5000)
    directionIds: list[int] = Field(default_factory=list, max_length=500)
    level: Level = "view"
    mode: Literal["replace", "raise"] = "replace"


def _department_names():
    from app.services.org import OrgSnapshot

    snap = OrgSnapshot()
    return {dep_id: " / ".join(snap.path(dep_id)) for dep_id in snap.departments}, snap


def _serialize_department(rule, paths, snap, projects, directions):
    members = 0
    if rule.department_id in snap.departments:
        branch = set(snap.subtree(rule.department_id))
        members = sum(1 for u in snap.users.values() if u.is_active and u.department_id in branch)
    return {
        "id": rule.id, "departmentId": rule.department_id, "departmentPath": paths.get(rule.department_id, ""),
        "departmentName": snap.departments[rule.department_id].name if rule.department_id in snap.departments else "",
        "memberCount": members, "projectId": rule.project_id, "directionId": rule.direction_id,
        "targetName": (projects[rule.project_id].title if rule.project_id in projects
                       else directions[rule.direction_id].name if rule.direction_id in directions else ""),
        "level": rule.level, "createdAt": iso(rule.created_at),
    }


@bp.get("/departments")
@admin_required
def list_department_rules():
    rules = db.session.execute(select(DepartmentAccessRule).order_by(DepartmentAccessRule.id)).scalars().all()
    _users, projects, directions = _lookups()
    paths, snap = _department_names()
    return jsonify([_serialize_department(r, paths, snap, projects, directions) for r in rules])


@bp.post("/departments")
@admin_required
def grant_departments():
    """Выдать подразделениям (со всеми вложенными) доступ к проектам и направлениям."""
    payload = DepartmentGrant.model_validate(request.get_json(silent=True) or {})
    dep_ids = list(dict.fromkeys(payload.departmentIds))
    project_ids = list(dict.fromkeys(payload.projectIds))
    direction_ids = list(dict.fromkeys(payload.directionIds))
    if not project_ids and not direction_ids:
        raise ApiError(400, "validation_error", "Выберите проекты или направления")
    _users, projects, directions = _lookups()
    paths, _snap = _department_names()
    for dep_id in dep_ids:
        if dep_id not in paths:
            raise ApiError(400, "validation_error", "Подразделение не найдено")
    targets = [("project_id", pid) for pid in project_ids] + [("direction_id", did) for did in direction_ids]
    for column, target_id in targets:
        if column == "project_id" and (target_id not in projects or projects[target_id].deleted_at is not None):
            raise ApiError(400, "validation_error", f"Проект #{target_id} не найден")
        if column == "direction_id" and target_id not in directions:
            raise ApiError(400, "validation_error", f"Направление #{target_id} не найдено")
    existing = {}
    for rule in db.session.execute(select(DepartmentAccessRule).where(DepartmentAccessRule.department_id.in_(dep_ids))).scalars():
        existing[(rule.department_id, "project_id" if rule.project_id else "direction_id",
                  rule.project_id or rule.direction_id)] = rule
    created = updated = unchanged = 0
    for dep_id in dep_ids:
        for column, target_id in targets:
            target_name = projects[target_id].title if column == "project_id" else directions[target_id].name
            entity = f"Подразделение «{paths[dep_id]}» → {target_name}"
            project = projects.get(target_id) if column == "project_id" else None
            rule = existing.get((dep_id, column, target_id))
            if rule is None:
                db.session.add(DepartmentAccessRule(department_id=dep_id, level=payload.level, **{column: target_id}))
                created += 1
                audit.record("Доступы", "create", entity, "level", "", LEVEL_LABELS[payload.level], project=project)
                continue
            new_level = payload.level if payload.mode == "replace" else best_level(rule.level, payload.level)
            if new_level == rule.level:
                unchanged += 1
                continue
            audit.record("Доступы", "update", entity, "level", LEVEL_LABELS[rule.level], LEVEL_LABELS[new_level],
                         project=project)
            rule.level = new_level
            updated += 1
    db.session.commit()
    return jsonify({"created": created, "updated": updated, "unchanged": unchanged}), 201


@bp.patch("/departments/<int:rule_id>")
@admin_required
def update_department_rule(rule_id):
    rule = db.session.get(DepartmentAccessRule, rule_id)
    if rule is None:
        raise ApiError(404, "not_found", "Правило не найдено")
    payload = RuleUpdate.model_validate(request.get_json(silent=True) or {})
    _users, projects, directions = _lookups()
    paths, snap = _department_names()
    if payload.level != rule.level:
        target = projects[rule.project_id].title if rule.project_id else directions[rule.direction_id].name
        audit.record("Доступы", "update", f"Подразделение «{paths.get(rule.department_id, '')}» → {target}", "level",
                     LEVEL_LABELS[rule.level], LEVEL_LABELS[payload.level],
                     project=projects.get(rule.project_id) if rule.project_id else None)
        rule.level = payload.level
        db.session.commit()
    return jsonify(_serialize_department(rule, paths, snap, projects, directions))


@bp.post("/departments/delete")
@admin_required
def delete_department_rules():
    payload = BulkDelete.model_validate(request.get_json(silent=True) or {})
    _users, projects, directions = _lookups()
    paths, _snap = _department_names()
    rules = db.session.execute(select(DepartmentAccessRule).where(DepartmentAccessRule.id.in_(payload.ids))).scalars().all()
    for rule in rules:
        target = projects[rule.project_id].title if rule.project_id else directions[rule.direction_id].name
        audit.record("Доступы", "delete", f"Подразделение «{paths.get(rule.department_id, '')}» → {target}", "level",
                     LEVEL_LABELS[rule.level], "", project=projects.get(rule.project_id) if rule.project_id else None)
        db.session.delete(rule)
    db.session.commit()
    return jsonify({"deleted": len(rules)})
