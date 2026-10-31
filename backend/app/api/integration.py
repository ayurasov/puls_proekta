"""API «Пульса» для «По Делу» — ссылки из задач на проекты и строки планов (ADR-0010).

Доступ — только по ключу «Пульса» (``Authorization: Bearer puls_…``), без cookie сессии и CSRF; только GET.
Контекст сотрудника «По Делу» — параметры ``userId`` (его id в «По Делу» = users.podelu_id), ``login``, ``email``:
проекты отдаются по тем же правилам доступа, что в интерфейсе «Пульса». Без контекста — все проекты (режим «все
проекты», который администратор «По Делу» включает явно).

* GET /api/integration/v1/ping
* GET /api/integration/v1/projects?q=&ids=&limit=&userId=&login=&email=
* GET /api/integration/v1/projects/<id>
* GET /api/integration/v1/projects/<id>/items?plan=&q=&limit=
"""

import re
from functools import wraps

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select

from app.api.projects import _default_plans, _task_stats
from app.extensions import csrf, db, limiter
from app.models import Direction, Plan, PlanItem, Project, User
from app.security import ApiError, accessible_projects_query, project_levels
from app.services import podelu_links
from app.version import APP_VERSION

bp = Blueprint("integration", __name__, url_prefix="/api/integration/v1")
csrf.exempt(bp)

PROJECTS_LIMIT, PROJECTS_MAX = 50, 200
ITEMS_LIMIT, ITEMS_MAX = 100, 500
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def _key_func():
    # Ограничение считается до проверки ключа — так подбор ключа ограничен так же, как обычные запросы
    return f"integration:{request.remote_addr}"


def integration_key_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        digest = podelu_links.verify_inbound(request.headers.get("Authorization"))
        if not digest:
            raise ApiError(401, "unauthorized", "Нужен действующий ключ «Пульса» (Authorization: Bearer puls_…) — "
                           "выпустите его в «Пульсе»: Администрирование → Интеграции → «По Делу»")
        g.integration_key = digest
        return view(*args, **kwargs)
    return wrapper


def _limit(name, default, maximum):
    try:
        return max(1, min(maximum, int(request.args.get(name, default))))
    except ValueError:
        return default


def _context_user():
    """(сотрудник «Пульса» или None, передан ли контекст). Поиск — по podelu_id, логину, email; только активные."""
    user_id = (request.args.get("userId") or "").strip()[:64]
    login = (request.args.get("login") or "").strip()[:150]
    email = (request.args.get("email") or "").strip()[:255]
    if not (user_id or login or email):
        return None, False
    active = select(User).where(User.is_active.is_(True))
    user = None
    if user_id and UUID_RE.match(user_id):
        user = db.session.execute(active.where(func.lower(User.podelu_id) == user_id.lower())).scalars().first()
    if user is None and login:
        user = db.session.execute(active.where(func.lower(User.login) == login.lower())).scalars().first()
    if user is None and email and "@" in email:
        user = db.session.execute(active.where(func.lower(User.email) == email.lower())).scalars().first()
    return user, True


def _visible_projects(user, given):
    """Проекты, которые видит сотрудник (или все — без контекста), без удалённых."""
    if given:
        if user is None:
            return []
        projects = db.session.execute(accessible_projects_query(user).order_by(Project.position, Project.id)).scalars().all()
        levels = project_levels(user, projects)
        return [p for p in projects if p.id in levels]
    return db.session.execute(select(Project).where(Project.deleted_at.is_(None))
                              .order_by(Project.position, Project.id)).scalars().all()


def _serialize(projects):
    ids = [p.id for p in projects]
    stats = _task_stats(ids) if ids else {}
    directions = {d.id: d.name for d in db.session.execute(
        select(Direction).where(Direction.id.in_({p.direction_id for p in projects if p.direction_id}))).scalars()} \
        if ids else {}
    out = []
    for p in projects:
        s = stats.get(p.id) or {}
        out.append({
            "id": p.id, "title": p.title, "subtitle": p.subtitle or "", "direction": directions.get(p.direction_id),
            "path": f"/projects/{p.id}", "progress": s.get("progress", 0), "start": s.get("start"), "end": s.get("end"),
            "overdue": s.get("overdue", 0), "total": s.get("total", 0), "done": s.get("done", 0),
            "planId": s.get("planId"), "planTitle": s.get("planTitle"), "nextMilestone": s.get("nextMilestone"),
        })
    return out


@bp.get("/ping")
@limiter.limit("120 per minute", key_func=_key_func)
@integration_key_required
def ping():
    return jsonify({"ok": True, "app": "puls", "name": "Пульс Проекта", "version": APP_VERSION})


@bp.get("/projects")
@limiter.limit("120 per minute", key_func=_key_func)
@integration_key_required
def projects():
    user, given = _context_user()
    found = _visible_projects(user, given)
    raw_ids = (request.args.get("ids") or "").strip()
    if raw_ids:
        wanted = {int(x) for x in raw_ids.split(",")[:PROJECTS_MAX] if x.strip().isdigit()}
        found = [p for p in found if p.id in wanted]
    query = " ".join((request.args.get("q") or "").split())[:200].lower()
    if query:
        def rank(p):
            title = p.title.lower()
            return (0 if title.startswith(query) else 1, title)
        found = sorted([p for p in found if query in p.title.lower() or query in (p.subtitle or "").lower()], key=rank)
    found = found[:_limit("limit", PROJECTS_LIMIT, PROJECTS_MAX)]
    return jsonify({"projects": _serialize(found), "userFound": (user is not None) if given else None})


def _project_or_404(project_id):
    user, given = _context_user()
    project = db.session.get(Project, project_id)
    if project is None or project.deleted_at is not None or (given and (
            user is None or project.id not in project_levels(user, [project]))):
        raise ApiError(404, "not_found", "Проект не найден или недоступен сотруднику")
    return project


@bp.get("/projects/<int:project_id>")
@limiter.limit("120 per minute", key_func=_key_func)
@integration_key_required
def project(project_id):
    p = _project_or_404(project_id)
    data = _serialize([p])[0]
    main = (_default_plans([p.id]).get(p.id) or (None, None))[0]
    plans = db.session.execute(select(Plan.id, Plan.title).where(
        Plan.project_id == p.id, Plan.deleted_at.is_(None), Plan.is_archived.is_(False)).order_by(Plan.position, Plan.id)).all()
    data["plans"] = [{"id": pid, "title": title, "main": pid == main} for pid, title in plans]
    return jsonify({"project": data})


@bp.get("/projects/<int:project_id>/items")
@limiter.limit("120 per minute", key_func=_key_func)
@integration_key_required
def items(project_id):
    p = _project_or_404(project_id)
    plan_arg = request.args.get("plan", "")
    if plan_arg:
        if not plan_arg.isdigit():
            raise ApiError(400, "validation_error", "Некорректный план")
        plan = db.session.get(Plan, int(plan_arg))
        if plan is None or plan.project_id != p.id or plan.deleted_at is not None:
            raise ApiError(404, "not_found", "План не найден")
    else:
        main = _default_plans([p.id]).get(p.id)
        if not main:
            return jsonify({"plan": None, "items": [], "truncated": False})
        plan = db.session.get(Plan, main[0])
    rows = db.session.execute(select(PlanItem).where(PlanItem.plan_id == plan.id, PlanItem.deleted_at.is_(None))
                              .order_by(PlanItem.position, PlanItem.id)).scalars().all()
    by_key = {r.key: r for r in rows}

    def group_path(item):
        names, seen, parent = [], set(), item.parent_key
        while parent and parent in by_key and parent not in seen and len(names) < 10:
            seen.add(parent)
            names.append(by_key[parent].name)
            parent = by_key[parent].parent_key
        return " / ".join(reversed(names))

    # Порядок как в плане: обход дерева от корня
    children = {}
    for r in rows:
        children.setdefault(r.parent_key if r.parent_key in by_key else None, []).append(r)
    ordered, stack = [], list(reversed(children.get(None, [])))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(children.get(node.key, [])))
    query = " ".join((request.args.get("q") or "").split())[:200].lower()
    if query:
        ordered = [r for r in ordered if query in (r.name or "").lower() or query in group_path(r).lower()]
    limit = _limit("limit", ITEMS_LIMIT, ITEMS_MAX)
    out = [{
        "key": r.key, "name": r.name, "type": r.type,
        "start": r.start_date.isoformat() if r.start_date else None,
        "end": (r.end_date or r.start_date).isoformat() if (r.end_date or r.start_date) else None,
        "status": r.status if r.type == "task" else None, "progress": r.progress or 0 if r.type == "task" else 0,
        "group": group_path(r), "path": f"/projects/{p.id}/{plan.id}?item={r.key}",
    } for r in ordered[:limit]]
    return jsonify({"plan": {"id": plan.id, "title": plan.title}, "items": out, "truncated": len(ordered) > limit})
