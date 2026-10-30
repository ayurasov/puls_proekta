"""Текущий пользователь, роли и уровни доступа к проектам (ADR-0004).

Роли сотрудника:

* admin (Администратор) — всё, включая сотрудников, доступы, корзину и настройки;
* manager (Менеджер) — создаёт проекты и направления, полностью управляет своими проектами;
* editor (Редактор) — работает в проектах по выданным правилам доступа;
* viewer (Наблюдатель) — только просмотр: уровень доступа ограничен «просмотром»,
  даже если правило выдаёт больше.

Уровни доступа к проекту: view < edit < manage. Правило выдаётся на проект или на всё
направление — сотруднику или подразделению (действует на всю ветку подразделения, ADR-0008);
действует наибольший уровень. Менеджер-автор проекта имеет manage.
"""

from functools import wraps

from flask import g, jsonify, session
from sqlalchemy import or_, select

from app.extensions import db
from app.models import AccessRule, Department, DepartmentAccessRule, Project, User

LEVEL_ORDER = {"": 0, None: 0, "view": 1, "edit": 2, "manage": 3}


class ApiError(Exception):
    """Ошибка API со стабильным кодом и человекочитаемым текстом."""

    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def response(self):
        body = {"error": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return jsonify(body), self.status


def best_level(*levels):
    best = ""
    for level in levels:
        if LEVEL_ORDER.get(level, 0) > LEVEL_ORDER.get(best, 0):
            best = level
    return best


def current_user():
    if "current_user" in g:
        return g.current_user
    user = None
    user_id = session.get("user_id")
    if user_id is not None:
        user = db.session.get(User, user_id)
        if user is not None and not user.is_active:
            user = None
    g.current_user = user
    return user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            raise ApiError(401, "auth_required", "Требуется вход в систему")
        return view(*args, **kwargs)

    return wrapper


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                raise ApiError(401, "auth_required", "Требуется вход в систему")
            if user.role not in roles:
                raise ApiError(403, "forbidden", "Недостаточно прав")
            return view(*args, **kwargs)

        return wrapper

    return decorator


admin_required = roles_required("admin")


def cap_level(user, level):
    if user.role == "viewer" and LEVEL_ORDER.get(level, 0) > LEVEL_ORDER["view"]:
        return "view"
    return level


def department_chain(user, parents=None):
    """Подразделение сотрудника и все вышестоящие: доступ, выданный подразделению, действует на всю его ветку.
    parents — {id: parent_id} всех подразделений, если вызывающий считает цепочки для многих сотрудников сразу."""
    if user is None or not user.department_id:
        return []
    cache = g.setdefault("department_chains", {})
    if user.id not in cache:
        out = []
        dep_id = user.department_id
        while dep_id and dep_id not in out and len(out) < 50:
            out.append(dep_id)
            dep_id = parents.get(dep_id) if parents is not None else \
                db.session.execute(select(Department.parent_id).where(Department.id == dep_id)).scalar()
        cache[user.id] = out
    return cache[user.id]


def project_levels(user, projects):
    """Уровень доступа сотрудника к каждому проекту из списка: {project_id: level}."""
    if user is None:
        return {}
    if user.role == "admin":
        return {p.id: "manage" for p in projects}
    rules = db.session.execute(select(AccessRule).where(AccessRule.user_id == user.id)).scalars().all()
    chain = department_chain(user)
    if chain:
        rules += db.session.execute(
            select(DepartmentAccessRule).where(DepartmentAccessRule.department_id.in_(chain))).scalars().all()
    by_project = {}
    by_direction = {}
    for rule in rules:
        if rule.project_id is not None:
            by_project[rule.project_id] = best_level(by_project.get(rule.project_id), rule.level)
        if rule.direction_id is not None:
            by_direction[rule.direction_id] = best_level(by_direction.get(rule.direction_id), rule.level)
    result = {}
    for project in projects:
        level = best_level(by_project.get(project.id), by_direction.get(project.direction_id))
        if user.role == "manager" and project.created_by_id == user.id:
            level = "manage"
        level = cap_level(user, level)
        if level:
            result[project.id] = level
    return result


def project_level(user, project):
    return project_levels(user, [project]).get(project.id, "")


def accessible_projects_query(user):
    """Проекты, которые сотрудник может открыть (без удалённых)."""
    query = select(Project).where(Project.deleted_at.is_(None))
    if user.role == "admin":
        return query
    direction_ids = select(AccessRule.direction_id).where(
        AccessRule.user_id == user.id, AccessRule.direction_id.is_not(None)
    )
    project_ids = select(AccessRule.project_id).where(AccessRule.user_id == user.id, AccessRule.project_id.is_not(None))
    conditions = [Project.id.in_(project_ids), Project.direction_id.in_(direction_ids)]
    chain = department_chain(user)
    if chain:
        conditions.append(Project.id.in_(select(DepartmentAccessRule.project_id).where(
            DepartmentAccessRule.department_id.in_(chain), DepartmentAccessRule.project_id.is_not(None))))
        conditions.append(Project.direction_id.in_(select(DepartmentAccessRule.direction_id).where(
            DepartmentAccessRule.department_id.in_(chain), DepartmentAccessRule.direction_id.is_not(None))))
    if user.role == "manager":
        conditions.append(Project.created_by_id == user.id)
    return query.where(or_(*conditions))


def require_project(project_id, minimum="view", allow_deleted=False):
    """Проект с проверкой доступа; (project, level). 404 скрывает чужие проекты."""
    user = current_user()
    project = db.session.get(Project, project_id)
    if project is None or (project.deleted_at is not None and not allow_deleted):
        raise ApiError(404, "not_found", "Проект не найден")
    level = project_level(user, project)
    if not level:
        raise ApiError(404, "not_found", "Проект не найден")
    if LEVEL_ORDER[level] < LEVEL_ORDER[minimum]:
        if minimum == "edit":
            raise ApiError(403, "read_only", "У вас доступ только для просмотра")
        raise ApiError(403, "forbidden", "Недостаточно прав для этого действия")
    return project, level
