"""Проекты: список с уровнями доступа, создание, изменение, порядок, корзина."""

from typing import Optional

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, case, func, select

from app import audit
from app.extensions import db
from app.models import Direction, Plan, PlanItem, Project, User, utcnow
from app.security import (
    ApiError, accessible_projects_query, admin_required, current_user, login_required, project_levels,
    require_project, roles_required,
)
from app.serializers import iso
from app.services.plan_doc import plan_meta
from app.services.plans import create_plan

bp = Blueprint("projects", __name__, url_prefix="/api")


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    subtitle: str = Field(default="", max_length=1000)
    directionId: Optional[int] = None

    @field_validator("title")
    @classmethod
    def _title(cls, v):
        v = " ".join(v.split())
        if not v:
            raise ValueError("Укажите название")
        return v


class ProjectUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    subtitle: Optional[str] = Field(default=None, max_length=1000)
    directionId: Optional[int] = None
    moveToNoDirection: bool = False

    @field_validator("title")
    @classmethod
    def _title(cls, v):
        if v is None:
            return v
        v = " ".join(v.split())
        if not v:
            raise ValueError("Укажите название")
        return v


class ReorderEntry(BaseModel):
    id: int
    directionId: Optional[int] = None
    # NaN и бесконечность ломают JSON списка проектов у всех, кто его видит
    position: float = Field(allow_inf_nan=False)


class ReorderIn(BaseModel):
    items: list[ReorderEntry]


def _default_plans(project_ids):
    """Основной план каждого проекта: первый открытый (не в архиве) по порядку вкладок.

    Копии плана и черновики — отдельные вкладки; если считать задачи всех планов, одна и та же
    задача в копии посчитается дважды, и процент на карточке разойдётся со «Статистикой» плана.
    """
    rows = db.session.execute(
        select(Plan.project_id, Plan.id, Plan.title)
        .where(Plan.project_id.in_(project_ids), Plan.deleted_at.is_(None), Plan.is_archived.is_(False))
        .order_by(Plan.position, Plan.id)
    ).all()
    plans = {}
    for project_id, plan_id, title in rows:
        plans.setdefault(project_id, (plan_id, title))
    return plans


def _task_stats(project_ids):
    """Задачи основного плана по статусам, средний прогресс, сроки и ближайшая веха — для карточек на главной.

    Средний прогресс считается так же, как «Средний прогресс» в окне «Статистика» плана:
    среднее поля «Прогресс» по всем задачам плана. Сроки (start, end) — самое раннее начало и самое
    позднее плановое окончание задач: по ним главная сортирует проекты «по сроку окончания».
    """
    if not project_ids:
        return {}
    default_plans = _default_plans(project_ids)
    plan_ids = [plan_id for plan_id, _title in default_plans.values()]
    if not plan_ids:
        return {}
    alive = and_(PlanItem.deleted_at.is_(None), PlanItem.plan_id.in_(plan_ids))
    rows = db.session.execute(
        select(
            Plan.project_id,
            func.count(PlanItem.id),
            func.sum(case((PlanItem.status == "done", 1), else_=0)),
            func.sum(case((PlanItem.status == "inwork", 1), else_=0)),
            func.sum(case((PlanItem.status == "blocked", 1), else_=0)),
            func.sum(case((and_(PlanItem.status != "done", PlanItem.end_date < func.current_date()), 1), else_=0)),
            func.sum(func.coalesce(PlanItem.progress, 0)),
            func.min(PlanItem.start_date),
            func.max(PlanItem.end_date),
        )
        .join(Plan, Plan.id == PlanItem.plan_id)
        .where(PlanItem.type == "task", alive)
        .group_by(Plan.project_id)
    ).all()

    def empty(pid):
        plan_id, title = default_plans[pid]
        return {"total": 0, "done": 0, "inwork": 0, "blocked": 0, "overdue": 0, "progress": 0,
                "start": None, "end": None, "planId": plan_id, "planTitle": title}

    stats = {}
    for pid, total, done, inwork, blocked, overdue, progress_sum, start, end in rows:
        entry = empty(pid)
        total = int(total or 0)
        entry.update({"total": total, "done": int(done or 0), "inwork": int(inwork or 0), "blocked": int(blocked or 0),
                      "overdue": int(overdue or 0),
                      "progress": round(float(progress_sum or 0) / total) if total else 0,
                      "start": _day(start), "end": _day(end)})
        stats[pid] = entry
    milestones = db.session.execute(
        select(Plan.project_id, PlanItem.name, PlanItem.start_date)
        .join(Plan, Plan.id == PlanItem.plan_id)
        .where(PlanItem.type == "milestone", alive, PlanItem.start_date >= func.current_date())
        .order_by(PlanItem.start_date)
    ).all()
    for pid, name, day in milestones:
        entry = stats.setdefault(pid, empty(pid))
        entry.setdefault("nextMilestone", {"name": name, "date": day.isoformat()})
    return stats


def _day(value):
    return value.isoformat() if value is not None else None


def serialize_project(project, level, plans=None, stats=None, creator=None):
    data = {
        "id": project.id,
        "title": project.title,
        "subtitle": project.subtitle or "",
        "directionId": project.direction_id,
        "position": project.position,
        "access": level,
        "createdAt": iso(project.created_at),
        "updatedAt": iso(project.updated_at),
        "createdById": project.created_by_id,
        "createdByName": creator.name if creator else None,
        "deletedAt": iso(project.deleted_at),
    }
    if plans is not None:
        data["plans"] = plans
    if stats is not None:
        data["stats"] = stats
    return data


@bp.get("/projects")
@login_required
def list_projects():
    user = current_user()
    projects = db.session.execute(accessible_projects_query(user).order_by(Project.position, Project.id)).scalars().all()
    levels = project_levels(user, projects)
    projects = [p for p in projects if p.id in levels]
    ids = [p.id for p in projects]
    plan_rows = db.session.execute(
        select(Plan.project_id, Plan.id, Plan.title, Plan.is_archived)
        .where(Plan.project_id.in_(ids), Plan.deleted_at.is_(None)).order_by(Plan.position, Plan.id)
    ).all() if ids else []
    plans = {}
    for project_id, plan_id, title, archived in plan_rows:
        if not archived:
            plans.setdefault(project_id, []).append({"id": plan_id, "title": title})
    stats = _task_stats(ids)
    creators = {u.id: u for u in db.session.execute(
        select(User).where(User.id.in_({p.created_by_id for p in projects if p.created_by_id}))).scalars()} if ids else {}
    return jsonify([
        serialize_project(p, levels[p.id], plans.get(p.id, []), stats.get(p.id), creators.get(p.created_by_id))
        for p in projects
    ])


@bp.post("/projects")
@roles_required("admin", "manager")
def create_project():
    user = current_user()
    payload = ProjectCreate.model_validate(request.get_json(silent=True) or {})
    if payload.directionId is not None and db.session.get(Direction, payload.directionId) is None:
        raise ApiError(400, "validation_error", "Направление не найдено")
    same_direction = (Project.direction_id.is_(None) if payload.directionId is None
                      else Project.direction_id == payload.directionId)
    last = db.session.execute(select(func.max(Project.position)).where(same_direction)).scalar() or 0
    project = Project(title=payload.title, subtitle=payload.subtitle.strip(), direction_id=payload.directionId,
                      position=last + 1, created_by_id=user.id)
    db.session.add(project)
    db.session.flush()
    plan = create_plan(project, user, payload.title)
    audit.record("Проекты", "create", f"project #{project.id} ({project.title})", "title", "", project.title,
                 project=project)
    db.session.commit()
    return jsonify(serialize_project(project, "manage", [{"id": plan.id, "title": plan.title}])), 201


@bp.get("/projects/<int:project_id>")
@login_required
def get_project(project_id):
    project, level = require_project(project_id)
    plans = db.session.execute(
        select(Plan).where(Plan.project_id == project.id, Plan.deleted_at.is_(None)).order_by(Plan.position, Plan.id)
    ).scalars().all()
    direction = db.session.get(Direction, project.direction_id) if project.direction_id else None
    data = serialize_project(project, level, [plan_meta(p) for p in plans])
    data["directionName"] = direction.name if direction else None
    return jsonify(data)


@bp.patch("/projects/<int:project_id>")
@login_required
def update_project(project_id):
    project, level = require_project(project_id, "manage")
    payload = ProjectUpdate.model_validate(request.get_json(silent=True) or {})
    entity = f"project #{project.id} ({project.title})"
    changed = False
    if payload.title is not None and payload.title != project.title:
        audit.record("Проекты", "update", entity, "title", project.title, payload.title, project=project)
        project.title = payload.title
        changed = True
    if payload.subtitle is not None and payload.subtitle.strip() != (project.subtitle or ""):
        audit.record("Проекты", "update", entity, "subtitle", project.subtitle, payload.subtitle.strip(), project=project)
        project.subtitle = payload.subtitle.strip()
        changed = True
    new_direction = None if payload.moveToNoDirection else payload.directionId
    if (payload.directionId is not None or payload.moveToNoDirection) and new_direction != project.direction_id:
        if current_user().role not in ("admin", "manager"):
            raise ApiError(403, "forbidden", "Переносить проекты между направлениями могут администратор и менеджер")
        if new_direction is not None and db.session.get(Direction, new_direction) is None:
            raise ApiError(400, "validation_error", "Направление не найдено")
        old_dir = db.session.get(Direction, project.direction_id) if project.direction_id else None
        new_dir = db.session.get(Direction, new_direction) if new_direction else None
        audit.record("Проекты", "update", entity, "direction", old_dir.name if old_dir else "Без направления",
                     new_dir.name if new_dir else "Без направления", project=project)
        project.direction_id = new_direction
        changed = True
    if changed:
        project.updated_at = utcnow()
    db.session.commit()
    return jsonify(serialize_project(project, level))


@bp.post("/projects/reorder")
@roles_required("admin", "manager")
def reorder_projects():
    payload = ReorderIn.model_validate(request.get_json(silent=True) or {})
    user = current_user()
    ids = [entry.id for entry in payload.items]
    projects = {p.id: p for p in db.session.execute(select(Project).where(Project.id.in_(ids))).scalars()}
    levels = project_levels(user, list(projects.values()))
    for entry in payload.items:
        project = projects.get(entry.id)
        if project is None or project.deleted_at is not None:
            continue
        if levels.get(project.id) != "manage":
            raise ApiError(403, "forbidden", f"Нет прав на проект «{project.title}»")
        if entry.directionId != project.direction_id:
            if entry.directionId is not None and db.session.get(Direction, entry.directionId) is None:
                raise ApiError(400, "validation_error", "Направление не найдено")
            old_dir = db.session.get(Direction, project.direction_id) if project.direction_id else None
            new_dir = db.session.get(Direction, entry.directionId) if entry.directionId else None
            audit.record("Проекты", "update", f"project #{project.id} ({project.title})", "direction",
                         old_dir.name if old_dir else "Без направления", new_dir.name if new_dir else "Без направления",
                         project=project)
            project.direction_id = entry.directionId
        project.position = entry.position
    db.session.commit()
    return jsonify({"ok": True})


@bp.delete("/projects/<int:project_id>")
@admin_required
def delete_project(project_id):
    """Проект уходит в корзину; удалить навсегда можно только из корзины."""
    project, _ = require_project(project_id, "manage")
    project.deleted_at = utcnow()
    project.deleted_by_id = current_user().id
    audit.record("Проекты", "delete", f"project #{project.id} ({project.title})", "", project.title, "в корзину",
                 project=project)
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/projects/<int:project_id>/restore")
@admin_required
def restore_project(project_id):
    project = db.session.get(Project, project_id)
    if project is None or project.deleted_at is None:
        raise ApiError(404, "not_found", "Проекта нет в корзине")
    project.deleted_at = None
    project.deleted_by_id = None
    if project.direction_id is not None and db.session.get(Direction, project.direction_id) is None:
        project.direction_id = None
    audit.record("Проекты", "restore", f"project #{project.id} ({project.title})", "", "корзина", project.title,
                 project=project)
    db.session.commit()
    return jsonify({"ok": True})


@bp.get("/trash")
@login_required
def trash():
    """Корзина. Администратор видит всё; остальные — удалённые планы проектов, которыми полностью
    управляют (вернуть план может «полное управление», удалить навсегда — только администратор)."""
    user = current_user()
    is_admin = user.role == "admin"
    projects = db.session.execute(
        select(Project).where(Project.deleted_at.is_not(None)).order_by(Project.deleted_at.desc())
    ).scalars().all() if is_admin else []
    plans = db.session.execute(
        select(Plan, Project).join(Project, Project.id == Plan.project_id)
        .where(Plan.deleted_at.is_not(None), Project.deleted_at.is_(None)).order_by(Plan.deleted_at.desc())
    ).all()
    if not is_admin:
        levels = project_levels(user, list({pr.id: pr for _pl, pr in plans}.values()))
        plans = [(pl, pr) for pl, pr in plans if levels.get(pr.id) == "manage"]
    user_ids = {p.deleted_by_id for p in projects} | {pl.deleted_by_id for pl, _ in plans}
    names = {u.id: u.name for u in db.session.execute(select(User).where(User.id.in_(user_ids - {None})))
             .scalars()} if user_ids - {None} else {}
    return jsonify({
        "projects": [{**serialize_project(p, "manage"), "deletedByName": names.get(p.deleted_by_id)} for p in projects],
        "plans": [{"id": pl.id, "title": pl.title, "projectId": pr.id, "projectTitle": pr.title,
                   "isArchived": pl.is_archived,
                   "deletedAt": iso(pl.deleted_at), "deletedByName": names.get(pl.deleted_by_id)} for pl, pr in plans],
        "canPurge": is_admin,
    })


@bp.delete("/trash/projects/<int:project_id>")
@admin_required
def purge_project(project_id):
    project = db.session.get(Project, project_id)
    if project is None or project.deleted_at is None:
        raise ApiError(404, "not_found", "Проекта нет в корзине")
    audit.record("Проекты", "purge", f"project #{project.id} ({project.title})", "", project.title,
                 "удалён навсегда", project=project)
    db.session.delete(project)
    db.session.commit()
    return jsonify({"ok": True})
