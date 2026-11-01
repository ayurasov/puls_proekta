"""API интеграций «Пульса» — для «По Делу» (ADR-0010), «Дашбордов компании» и других систем (ADR-0011).

Доступ — только по ключу интеграции (``Authorization: Bearer puls_…``), без cookie сессии и CSRF; только GET.
Каждый ключ — у своей системы, со своими правами (scopes) и своим ограничением частоты: 120 запросов в минуту на
ключ; запросы без действующего ключа ограничиваются по адресу (подбор ключа не получает отдельного счётчика на
каждую попытку).

Контекст сотрудника — параметры ``userId`` (id в «По Делу» = users.podelu_id), ``login``, ``email``: проекты
отдаются по тем же правилам доступа, что в интерфейсе «Пульса». Без контекста — все проекты.

* GET /api/integration/v1/ping — любой действующий ключ
* GET /api/integration/v1/projects?q=&ids=&limit=&userId=&login=&email= — projects.read; с projects.stats ещё
  ``inwork``, ``blocked``, ``directionInfo {id, name}``, ``updatedAt``, ``planWindow {start, end}``
* GET /api/integration/v1/projects/<id> — projects.read (с projects.stats — те же поля)
* GET /api/integration/v1/projects/<id>/items?plan=&q=&key=&limit= — projects.read
* GET /api/integration/v1/directions?userId=&login=&email= — projects.read: направления с числом проектов
"""

import re
from functools import wraps

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select

from app.api.projects import _default_plans, _task_stats
from app.extensions import csrf, db, limiter
from app.models import Direction, Plan, PlanItem, Project, User
from app.security import ApiError, accessible_projects_query, project_levels
from app.services import integration_keys
from app.version import APP_VERSION

bp = Blueprint("integration", __name__, url_prefix="/api/integration/v1")
csrf.exempt(bp)

PROJECTS_LIMIT, PROJECTS_MAX = 50, 200
ITEMS_LIMIT, ITEMS_MAX = 100, 500
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


RATE = "120 per minute"
_MISSING = object()


def _presented_key():
    """Действующий ключ запроса (один поиск на запрос — его используют и ограничение частоты, и проверка)."""
    row = g.get("integration_key", _MISSING)
    if row is _MISSING:
        row = integration_keys.find(integration_keys.bearer(request.headers.get("Authorization")))
        g.integration_key = row
    return row


def _key_func():
    """Счётчик частоты — свой у каждого ключа; запросы без действующего ключа — общий счётчик адреса."""
    row = _presented_key()
    return f"integration-key:{row.id}" if row is not None else f"integration-ip:{request.remote_addr}"


def integration_key_required(scope=None):
    """Действующий ключ интеграции, а для scope — ещё и это право у ключа (иначе 403 insufficient_scope)."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            row = _presented_key()
            if row is None:
                raise ApiError(401, "unauthorized", "Нужен действующий ключ «Пульса» (Authorization: Bearer puls_…) — "
                               "его выпускает администратор «Пульса»: Администрирование → Интеграции → Ключи доступа")
            if scope and not integration_keys.has_scope(row, scope):
                raise ApiError(403, "insufficient_scope", f"У ключа «{row.name}» нет права {scope}")
            integration_keys.touch(row)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def _stats_allowed():
    return integration_keys.has_scope(g.integration_key, "projects.stats")


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


def _iso(value):
    return value.replace(microsecond=0).isoformat() + "Z" if value else None


def _serialize(projects, stats_scope=False):
    """Проект для другой системы. Поля projects.read не меняются от версии к версии («По Делу» на них опирается);
    projects.stats добавляет новые поля, не меняя прежних (направление строкой остаётся в ``direction``)."""
    ids = [p.id for p in projects]
    stats = _task_stats(ids) if ids else {}
    directions = {d.id: d.name for d in db.session.execute(
        select(Direction).where(Direction.id.in_({p.direction_id for p in projects if p.direction_id}))).scalars()} \
        if ids else {}
    plans = {}
    if stats_scope:
        plan_ids = [s["planId"] for s in stats.values() if s.get("planId")]
        plans = {pl.id: pl for pl in db.session.execute(select(Plan).where(Plan.id.in_(plan_ids))).scalars()} \
            if plan_ids else {}
    out = []
    for p in projects:
        s = stats.get(p.id) or {}
        data = {
            "id": p.id, "title": p.title, "subtitle": p.subtitle or "", "direction": directions.get(p.direction_id),
            "path": f"/projects/{p.id}", "progress": s.get("progress", 0), "start": s.get("start"), "end": s.get("end"),
            "overdue": s.get("overdue", 0), "total": s.get("total", 0), "done": s.get("done", 0),
            "planId": s.get("planId"), "planTitle": s.get("planTitle"), "nextMilestone": s.get("nextMilestone"),
        }
        if stats_scope:
            plan = plans.get(s.get("planId"))
            window = ((plan.settings or {}).get("range") or {}) if plan is not None else {}
            changed = max([t for t in (p.updated_at, plan.updated_at if plan is not None else None) if t], default=None)
            data.update({
                "inwork": s.get("inwork", 0), "blocked": s.get("blocked", 0),
                "directionInfo": {"id": p.direction_id, "name": directions[p.direction_id]}
                if p.direction_id in directions else None,
                "updatedAt": _iso(changed),
                "planWindow": {"start": window.get("start"), "end": window.get("end")} if plan is not None else None,
            })
        out.append(data)
    return out


@bp.get("/ping")
@limiter.limit(RATE, key_func=_key_func)
@integration_key_required()
def ping():
    key = g.integration_key
    return jsonify({"ok": True, "app": "puls", "name": "Пульс Проекта", "version": APP_VERSION,
                    "key": {"name": key.name, "scopes": list(key.scopes or [])}})


@bp.get("/projects")
@limiter.limit(RATE, key_func=_key_func)
@integration_key_required("projects.read")
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
    return jsonify({"projects": _serialize(found, _stats_allowed()), "userFound": (user is not None) if given else None})


def _project_or_404(project_id):
    user, given = _context_user()
    project = db.session.get(Project, project_id)
    if project is None or project.deleted_at is not None or (given and (
            user is None or project.id not in project_levels(user, [project]))):
        raise ApiError(404, "not_found", "Проект не найден или недоступен сотруднику")
    return project


@bp.get("/projects/<int:project_id>")
@limiter.limit(RATE, key_func=_key_func)
@integration_key_required("projects.read")
def project(project_id):
    p = _project_or_404(project_id)
    data = _serialize([p], _stats_allowed())[0]
    main = (_default_plans([p.id]).get(p.id) or (None, None))[0]
    plans = db.session.execute(select(Plan.id, Plan.title).where(
        Plan.project_id == p.id, Plan.deleted_at.is_(None), Plan.is_archived.is_(False)).order_by(Plan.position, Plan.id)).all()
    data["plans"] = [{"id": pid, "title": title, "main": pid == main} for pid, title in plans]
    return jsonify({"project": data})


@bp.get("/projects/<int:project_id>/items")
@limiter.limit(RATE, key_func=_key_func)
@integration_key_required("projects.read")
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
    # key — ровно одна строка (проверка ссылки в «По Делу» не зависит от размера плана)
    key = (request.args.get("key") or "").strip()[:64]
    if key:
        ordered = [r for r in ordered if r.key == key]
    limit = _limit("limit", ITEMS_LIMIT, ITEMS_MAX)
    out = [{
        "key": r.key, "name": r.name, "type": r.type,
        "start": r.start_date.isoformat() if r.start_date else None,
        "end": (r.end_date or r.start_date).isoformat() if (r.end_date or r.start_date) else None,
        "status": r.status if r.type == "task" else None, "progress": r.progress or 0 if r.type == "task" else 0,
        "group": group_path(r), "path": f"/projects/{p.id}/{plan.id}?item={r.key}",
    } for r in ordered[:limit]]
    return jsonify({"plan": {"id": plan.id, "title": plan.title}, "items": out, "truncated": len(ordered) > limit})


@bp.get("/directions")
@limiter.limit(RATE, key_func=_key_func)
@integration_key_required("projects.read")
def directions():
    """Направления с проектами, которые видит сотрудник (без контекста — все): [{id, name, color, position,
    projects}]. Направление без доступных сотруднику проектов не показывается — как на главной «Пульса»."""
    user, given = _context_user()
    visible = _visible_projects(user, given)
    counts = {}
    for p in visible:
        counts[p.direction_id] = counts.get(p.direction_id, 0) + 1
    rows = db.session.execute(select(Direction).order_by(Direction.position, Direction.id)).scalars().all()
    out = [{"id": d.id, "name": d.name, "color": d.color, "position": d.position, "projects": counts.get(d.id, 0)}
           for d in rows if counts.get(d.id) or not given]
    return jsonify({"directions": out, "withoutDirection": counts.get(None, 0),
                    "userFound": (user is not None) if given else None})
