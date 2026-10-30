"""Структура компании (ADR-0008) — те же правила, что в todo-planner «По Делу».

* Подразделения образуют дерево; у подразделения есть тип (компания, дирекция, отдел…) и порядок среди
  соседних. Названия уникальны среди соседей (без учёта регистра).
* У подразделения может быть несколько руководителей; основной — непосредственный руководитель сотрудников
  подразделения.
* Сотрудник входит в одно подразделение. Его непосредственный руководитель:
  1) никто, если он «первое лицо»;
  2) явно назначенный руководитель (активный);
  3) иначе — основной руководитель его подразделения (если это не он сам);
  4) если он сам руководит своим подразделением или руководителя нет — то же для родительского и выше.
"""

import uuid

from sqlalchemy import select, text

from app.extensions import db
from app.models import AppSetting, Department, DepartmentHead, User

DEFAULT_UNIT_KINDS = [
    {"value": "company", "label": "Компания", "color": "#7c3aed"},
    {"value": "division", "label": "Дирекция", "color": "#0369a1"},
    {"value": "direction", "label": "Направление", "color": "#0f766e"},
    {"value": "service", "label": "Служба", "color": "#b45309"},
    {"value": "department", "label": "Отдел", "color": "#4f7cff"},
    {"value": "sector", "label": "Сектор", "color": "#be185d"},
    {"value": "group", "label": "Группа", "color": "#15803d"},
    {"value": "position", "label": "Позиция", "color": "#64748b"},
]
DEFAULT_MAX_DEPTH = 15
SETTING_KEY = "org"
ORG_LOCK_KEY = 820_301  # номер рекомендательной блокировки PostgreSQL для правок структуры


def new_id():
    return str(uuid.uuid4())


def norm(name):
    """Сравнение названий: без регистра и лишних пробелов."""
    return " ".join((name or "").split()).casefold()


# ---------- настройки ----------

def get_settings():
    row = db.session.get(AppSetting, SETTING_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    kinds = value.get("unitKinds") or DEFAULT_UNIT_KINDS
    depth = value.get("maxDepth") or DEFAULT_MAX_DEPTH
    return {"unitKinds": kinds, "maxDepth": depth}


def save_settings(settings):
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is None:
        db.session.add(AppSetting(key=SETTING_KEY, value=settings))
    else:
        row.value = settings


def kind_values():
    return {k["value"] for k in get_settings()["unitKinds"]}


# ---------- снимок структуры ----------

def lock_org():
    """Изменения структуры — по одному до конца транзакции: две одновременные правки, каждая допустимая сама
    по себе (переносы подразделений друг в друга, руководители), не должны вместе замкнуть цикл.
    PostgreSQL — рекомендательная блокировка транзакции, SQLite — BEGIN IMMEDIATE."""
    connection = db.session.connection()
    if connection.dialect.name == "postgresql":
        db.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ORG_LOCK_KEY})
    elif connection.dialect.name == "sqlite":
        raw = connection.connection.driver_connection
        if not raw.in_transaction:
            raw.execute("BEGIN IMMEDIATE")


class OrgSnapshot:
    """Структура целиком в памяти: подразделения, руководители, сотрудники (2000 сотрудников — мгновенно).
    Читается заново из базы (populate_existing): после lock_org снимок не берёт объекты, загруженные до блокировки."""

    def __init__(self, include_inactive=True):
        fresh = {"populate_existing": True}
        self.departments = {d.id: d for d in db.session.execute(select(Department).execution_options(**fresh)).scalars()}
        users = select(User) if include_inactive else select(User).where(User.is_active.is_(True))
        self.users = {u.id: u for u in db.session.execute(users.execution_options(**fresh)).scalars()}
        self.heads = {}  # department_id → [(user_id, is_primary)] — основной первым
        for row in db.session.execute(select(DepartmentHead).execution_options(**fresh)).scalars():
            self.heads.setdefault(row.department_id, []).append((row.user_id, row.is_primary))
        for heads in self.heads.values():
            heads.sort(key=lambda h: (not h[1], (self.users[h[0]].name if h[0] in self.users else "").casefold()))
        self.children = {}
        for dep in self.departments.values():
            self.children.setdefault(dep.parent_id, []).append(dep)
        for items in self.children.values():
            items.sort(key=lambda d: (d.sort_order or 0, d.name.casefold()))
        self.head_of = {}
        for dep_id, heads in self.heads.items():
            for user_id, _primary in heads:
                self.head_of.setdefault(user_id, []).append(dep_id)

    def path(self, dep_id):
        """Названия от корня до подразделения."""
        names, seen = [], set()
        while dep_id and dep_id in self.departments and dep_id not in seen:
            seen.add(dep_id)
            dep = self.departments[dep_id]
            names.append(dep.name)
            dep_id = dep.parent_id
        return list(reversed(names))

    def depth(self, dep_id):
        return len(self.path(dep_id))

    def subtree(self, dep_id):
        out, seen, stack = [], set(), [dep_id]
        while stack:
            current = stack.pop()
            if current in seen:  # защита от цикла в данных: обход не должен зависнуть
                continue
            seen.add(current)
            out.append(current)
            stack.extend(d.id for d in self.children.get(current, []))
        return out

    def department_cycle(self):
        """Подразделения, которые оказались вложены сами в себя (не должно быть никогда)."""
        bad = set()
        for dep_id in self.departments:
            seen, current = set(), dep_id
            while current is not None and current in self.departments:
                if current in seen:
                    bad.add(dep_id)
                    break
                seen.add(current)
                current = self.departments[current].parent_id
        return bad

    def reporting_cycle(self):
        """Сотрудники, замыкающие цепочку подчинения (только участники цикла, не их подчинённые)."""
        manager_of = {uid: self.direct_manager(user)[0] for uid, user in self.users.items() if user.is_active}
        members, done = set(), set()
        for start in manager_of:
            path, position, current = [], {}, start
            while current is not None and current not in done and current in manager_of:
                if current in position:
                    members.update(path[position[current]:])
                    break
                position[current] = len(path)
                path.append(current)
                current = manager_of[current]
            done.update(path)
        return members

    def ordered(self):
        """Подразделения сверху вниз: родитель раньше детей, соседи — по порядку и названию."""
        out = []

        def walk(parent):
            for dep in self.children.get(parent, []):
                out.append(dep)
                walk(dep.id)
        walk(None)
        return out

    def primary_head(self, dep_id, active_only=True):
        for user_id, _primary in self.heads.get(dep_id, []):
            user = self.users.get(user_id)
            if user is not None and (user.is_active or not active_only):
                return user_id
        return None

    def structure_manager(self, user):
        """Руководитель по структуре (без учёта явного назначения)."""
        if user.department_id and user.department_id in self.departments:
            dep_id = user.department_id
        elif self.head_of.get(user.id):
            dep_id = self.departments[self.head_of[user.id][0]].parent_id \
                if self.head_of[user.id][0] in self.departments else None
        else:
            return None
        seen = set()
        while dep_id and dep_id not in seen:
            seen.add(dep_id)
            head = self.primary_head(dep_id)
            if head and head != user.id:
                return head
            dep_id = self.departments[dep_id].parent_id if dep_id in self.departments else None
        return None

    def direct_manager(self, user):
        """(id руководителя, источник: explicit | structure | none)."""
        if user.reports_to_none:
            return None, "none"
        if user.manager_id and user.manager_id != user.id:
            manager = self.users.get(user.manager_id)
            if manager is not None and manager.is_active:
                return manager.id, "explicit"
        manager_id = self.structure_manager(user)
        return manager_id, ("structure" if manager_id else "none")

    def chain(self, user, limit=50):
        out, seen, current = [], {user.id}, user
        while len(out) < limit:
            manager_id, _source = self.direct_manager(current)
            if not manager_id or manager_id in seen:
                return out
            seen.add(manager_id)
            out.append(manager_id)
            current = self.users[manager_id]
        return out

    def find_child(self, parent_id, name):
        key = norm(name)
        return next((d for d in self.children.get(parent_id, []) if norm(d.name) == key), None)

    def find_path(self, names):
        parent = None
        dep = None
        for name in names:
            dep = self.find_child(parent, name)
            if dep is None:
                return None
            parent = dep.id
        return dep


def assert_consistent():
    """Проверка перед сохранением любой правки структуры: ни подразделение не вложено само в себя, ни цепочка
    подчинения не замкнута. Иначе 409 и откат — назначение руководителей, перенос сотрудника или подразделения,
    включение сотрудника и импорт проверяются одинаково."""
    from app.security import ApiError

    db.session.flush()
    snap = OrgSnapshot()
    if snap.department_cycle():
        raise ApiError(409, "org_cycle", "Подразделение нельзя вложить в само себя")
    members = snap.reporting_cycle()
    if members:
        names = ", ".join(sorted((snap.users[m].name for m in members), key=str.casefold)[:5])
        raise ApiError(409, "org_cycle", f"Цикл подчинения: {names} — руководители подчиняются друг другу")


def would_cycle(user_id, manager_id, snapshot=None):
    """Назначение manager_id руководителем user_id замкнуло бы цепочку подчинения."""
    if manager_id is None:
        return False
    if manager_id == user_id:
        return True
    snap = snapshot or OrgSnapshot()
    user = snap.users.get(user_id)
    manager = snap.users.get(manager_id)
    if user is None or manager is None:
        return False
    original = user.manager_id, user.reports_to_none
    user.manager_id, user.reports_to_none = manager_id, False
    try:
        return user_id in snap.chain(manager)
    finally:
        user.manager_id, user.reports_to_none = original


def person_json(user, snap):
    manager_id, source = snap.direct_manager(user)
    return {
        "id": user.id, "name": user.name, "login": user.login, "position": user.position or "",
        "departmentId": user.department_id if user.department_id in snap.departments else None,
        "managerId": user.manager_id, "directManagerId": manager_id, "managerSource": source,
        "reportsToNone": bool(user.reports_to_none), "isActive": bool(user.is_active),
        "headOf": snap.head_of.get(user.id, []),
        "avatarUrl": f"/api/users/{user.id}/avatar?v={user.avatar_version}" if user.avatar_version else None,
    }


def department_json(dep, snap, counts=None):
    heads = snap.heads.get(dep.id, [])
    data = {
        "id": dep.id, "name": dep.name, "parentId": dep.parent_id, "kind": dep.kind, "sortOrder": dep.sort_order or 0,
        "orgSource": dep.org_source, "externalId": dep.external_id,
        "headIds": [h[0] for h in heads], "primaryHeadId": next((h[0] for h in heads if h[1]), None),
        "level": snap.depth(dep.id), "childCount": len(snap.children.get(dep.id, [])),
    }
    if counts is not None:
        data.update(counts.get(dep.id, {"employeeCount": 0, "branchEmployeeCount": 0}))
    return data


def employee_counts(snap):
    """Сотрудники подразделения и всей его ветки (активные)."""
    own = {}
    for user in snap.users.values():
        if user.is_active and user.department_id in snap.departments:
            own[user.department_id] = own.get(user.department_id, 0) + 1
    out = {}
    for dep_id in snap.departments:
        branch = sum(own.get(d, 0) for d in snap.subtree(dep_id))
        out[dep_id] = {"employeeCount": own.get(dep_id, 0), "branchEmployeeCount": branch}
    return out


def set_heads(dep_id, user_ids, primary_id):
    db.session.query(DepartmentHead).filter(DepartmentHead.department_id == dep_id).delete()
    for user_id in dict.fromkeys(user_ids):
        db.session.add(DepartmentHead(department_id=dep_id, user_id=user_id, is_primary=user_id == primary_id))
