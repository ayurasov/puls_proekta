"""Быстрый импорт структуры компании из CSV и общий планировщик для синхронизации с AD.

Те же правила и тот же формат файла, что в todo-planner «По Делу» (ADR-0044, этап 4), —
структура у инструментов общая (ADR-0008). Оба источника приводятся к строкам
«сотрудник → подразделение, руководитель», а дальше работает один планировщик:

1. ``plan_import(rows)`` — без записи в БД: какие подразделения появятся (по пути «Компания /
   Дирекция / Отдел»), кого создать, кому что поменять, ошибки по строкам и циклы
   подчинения, которые получились бы после импорта;
2. ``apply_import(rows)`` — тот же план в одной транзакции; при ошибках ничего не меняется.

Сотрудник ищется по внешнему ключу (источник + external_id), затем по email, затем по логину
(без учёта регистра). Явный руководитель записывается, только если он отличается от
руководителя по структуре: тогда последующие переводы и смены руководителей подразделений
продолжают работать сами. Руководители подразделений только добавляются (основным — если
основного ещё нет). Новые сотрудники создаются без пароля, с ролью «Наблюдатель»: войти они
смогут, когда администратор задаст пароль или включит вход через домен.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import Department, DepartmentHead, User
from app.services import org
from app.services.org import OrgSnapshot, norm

MAX_ROWS = 5000
MAX_NAME = 200
PATH_SPLIT = re.compile(r"\s*(?:/|>|\\)\s*")
TRUE_VALUES = {"1", "true", "yes", "да", "y", "+", "руководитель", "head"}

# Заголовки столбцов: английские и русские варианты — как в todo-planner
COLUMN_ALIASES = {
    "email": ("email", "e-mail", "mail", "почта", "эл. почта", "электронная почта"),
    "name": ("name", "фио", "имя", "сотрудник", "displayname"),
    "login": ("login", "логин", "samaccountname", "учётная запись", "учетная запись"),
    "department": ("department", "department_path", "подразделение", "отдел", "путь подразделения"),
    "position": ("position", "title", "должность"),
    "manager_email": ("manager_email", "manager", "руководитель", "email руководителя", "почта руководителя"),
    "is_head": ("is_head", "head", "руководит", "руководитель подразделения"),
    "external_id": ("external_id", "id", "табельный номер", "табельный", "objectguid"),
}
SOURCE_COMMENT = {"csv": "импорт CSV", "ad": "синхронизация с AD"}


class ImportFormatError(ValueError):
    """Файл не разобран: нет нужных столбцов, пустой или слишком большой."""


def clean(value):
    """Неразрывные и повторные пробелы не нужны (как в карточке сотрудника)."""
    return " ".join(str(value or "").replace("\xa0", " ").split())


@dataclass
class Row:
    line: int
    email: str = ""
    name: str = ""
    login: str = ""
    department: list = field(default_factory=list)   # путь от корня
    position: str | None = None
    manager_email: str = ""
    manager_ref: str = ""                              # AD: DN руководителя
    is_head: bool = False
    external_id: str = ""
    disabled: bool = False


# ---------------------------------------------------------------- разбор CSV
def split_path(value):
    return [clean(part)[:MAX_NAME] for part in PATH_SPLIT.split(str(value or "").strip()) if clean(part)]


def detect_delimiter(text):
    sample = text[:4096]
    return ";" if sample.count(";") >= sample.count(",") else ","


def _header_map(header, aliases):
    mapping = {}
    for index, title in enumerate(header):
        key = (title or "").strip().lower().lstrip("\ufeff")
        for column, names in aliases.items():
            if key in names and column not in mapping:
                mapping[column] = index
    return mapping


def _guarded(reader, error=None):
    """Строки CSV; испорченный файл (поле больше 128 КБ, незакрытые кавычки) — понятная ошибка, а не 500."""
    try:
        yield from reader
    except csv.Error as exc:
        raise (error or ImportFormatError)(f"Не удалось разобрать CSV: {exc}") from None


def parse_csv(text):
    """CSV (разделитель «;» или «,», первая строка — заголовки) → список Row."""
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        raise ImportFormatError("Файл пустой")
    reader = _guarded(csv.reader(io.StringIO(text), delimiter=detect_delimiter(text)))
    header = next(reader, None) or []
    mapping = _header_map(header, COLUMN_ALIASES)
    if "email" not in mapping and "login" not in mapping:
        raise ImportFormatError("Нужен столбец email или login (логин)")
    if "department" not in mapping:
        raise ImportFormatError("Нужен столбец department (подразделение, путь через «/»)")
    rows = []
    for line, values in enumerate(reader, start=2):
        if not any((value or "").strip() for value in values):
            continue
        if len(rows) >= MAX_ROWS:
            raise ImportFormatError(f"Не больше {MAX_ROWS} строк за один импорт")

        def cell(column, values=values):
            index = mapping.get(column)
            return (values[index] if index is not None and index < len(values) else "").strip()

        rows.append(Row(
            line=line,
            email=cell("email").lower()[:255],
            name=clean(cell("name"))[:255],
            login=cell("login")[:150],
            department=split_path(cell("department")),
            position=clean(cell("position"))[:500] or None,
            manager_email=cell("manager_email").lower()[:255],
            is_head=cell("is_head").lower() in TRUE_VALUES,
            external_id=cell("external_id")[:255],
        ))
    return rows


# ---------------------------------------------------------------- структура «после импорта»
@dataclass
class FuturePerson:
    name: str
    department_id: object = None
    manager_id: object = None
    active: bool = True
    reports_to_none: bool = False


class FutureOrg:
    """Структура в памяти с правилами OrgSnapshot (services/org.py): по ней планировщики проверяют,
    отличается ли руководитель из файла от руководителя по структуре и не получится ли цикл подчинения.
    Ключи — id существующих подразделений и сотрудников или ключи новых («new:1», «new-user:5»)."""

    def __init__(self, snap):
        self.parent = {dep_id: dep.parent_id for dep_id, dep in snap.departments.items()}
        self.people = {
            user.id: FuturePerson(user.name, user.department_id if user.department_id in snap.departments else None,
                                  user.manager_id, bool(user.is_active), bool(user.reports_to_none))
            for user in snap.users.values()
        }
        self.heads = {dep_id: [[user_id, primary] for user_id, primary in heads] for dep_id, heads in snap.heads.items()}
        self.headed = {}
        for dep_id, heads in self.heads.items():
            for user_id, _primary in heads:
                self.headed.setdefault(user_id, []).append(dep_id)

    def add_unit(self, key, parent):
        self.parent[key] = parent

    def _sort(self, dep_id):
        self.heads[dep_id].sort(key=lambda h: (not h[1], (self.people[h[0]].name if h[0] in self.people else "").casefold()))

    def add_head(self, dep_id, key, primary=None):
        """Добавить руководителя; основным — если так указано и основного ещё нет (primary=None — если нет основного)."""
        heads = self.heads.setdefault(dep_id, [])
        if any(h[0] == key for h in heads):
            return
        has_primary = any(h[1] for h in heads)
        heads.append([key, (not has_primary) if primary is None else (bool(primary) and not has_primary)])
        self.headed.setdefault(key, []).append(dep_id)
        self._sort(dep_id)

    def clear_heads(self, key):
        for dep_id in self.headed.pop(key, []):
            self.heads[dep_id] = [h for h in self.heads.get(dep_id, []) if h[0] != key]

    def primary_head(self, dep_id):
        for key, _primary in self.heads.get(dep_id, []):
            person = self.people.get(key)
            if person is not None and person.active:
                return key
        return None

    def structure_manager(self, key):
        person = self.people.get(key)
        if person is None:
            return None
        if person.department_id is not None and person.department_id in self.parent:
            dep_id = person.department_id
        elif self.headed.get(key):
            dep_id = self.parent.get(self.headed[key][0])
        else:
            return None
        seen = set()
        while dep_id is not None and dep_id not in seen:
            seen.add(dep_id)
            head = self.primary_head(dep_id)
            if head is not None and head != key:
                return head
            dep_id = self.parent.get(dep_id)
        return None

    def direct_manager(self, key):
        person = self.people.get(key)
        if person is None or person.reports_to_none:
            return None
        if person.manager_id is not None and person.manager_id != key:
            manager = self.people.get(person.manager_id)
            if manager is not None and manager.active:
                return person.manager_id
        return self.structure_manager(key)

    def cycles(self):
        """Сотрудники, замыкающие цепочку руководителей, — только участники цикла: их подчинённые к ошибке
        не относятся и загрузке своих строк не мешают."""
        manager_of = {key: self.direct_manager(key) for key in self.people}
        members, done = set(), set()
        for start in self.people:
            path, position, current = [], {}, start
            while current is not None and current not in done:
                if current in position:
                    members.update(path[position[current]:])
                    break
                position[current] = len(path)
                path.append(current)
                current = manager_of.get(current)
            done.update(path)
        return members


def unique_login(base, taken):
    """Логин нового сотрудника: латиница, цифры, «._-»; занятый — с числом в конце."""
    base = re.sub(r"[^a-z0-9._-]", "", (base or "user").lower())[:140] or "user"
    login, counter = base, 1
    while login in taken:
        counter += 1
        login = f"{base}{counter}"
    taken.add(login)
    return login


# ---------------------------------------------------------------- планирование
@dataclass
class ImportPlan:
    source: str
    departments: list = field(default_factory=list)   # [{key, path, name, parentKey, kind}]
    users: list = field(default_factory=list)         # [{line, userId, name, email, action, changes, errors}]
    deactivate: list = field(default_factory=list)    # [{userId, name}]
    cycles: list = field(default_factory=list)
    errors: int = 0
    targets: dict = field(default_factory=dict)       # ключ сотрудника → что записать

    def as_dict(self):
        actions = [user["action"] for user in self.users]
        return {
            "source": self.source,
            "summary": {
                "rows": len(self.users),
                "createDepartments": len(self.departments),
                "createUsers": actions.count("create"),
                "updateUsers": actions.count("update"),
                "unchanged": actions.count("unchanged"),
                "deactivateUsers": len(self.deactivate),
                "errors": self.errors,
            },
            "departments": [{k: v for k, v in item.items() if k != "parentKey"} for item in self.departments],
            "users": self.users,
            "deactivate": self.deactivate,
            "cycles": self.cycles,
            "canApply": self.errors == 0,
        }


def _resolve_departments(rows, plan, snap):
    """Путь подразделения → id существующего или ключ нового ("new:<n>")."""
    children = {(dep.parent_id, norm(dep.name)): dep.id for dep in snap.departments.values()}
    has_roots = any(dep.parent_id is None for dep in snap.departments.values())
    kinds = [kind["value"] for kind in org.get_settings()["unitKinds"]]

    def known(kind):
        return kind if kind in kinds else kinds[0]

    resolved = {}
    for row in rows:
        parent, path = None, []
        for depth, name in enumerate(row.department):
            path.append(name)
            key = (parent, norm(name))
            if key not in children:
                new_key = f"new:{len(plan.departments) + 1}"
                kind = known("company" if depth == 0 and not has_roots else "department")
                plan.departments.append({"key": new_key, "path": " / ".join(path), "name": name, "parentKey": parent,
                                         "kind": kind})
                children[key] = new_key
            parent = children[key]
        resolved[row.line] = parent
    return resolved


def _match_users(rows, users, source):
    by_external, by_email, by_login = {}, {}, {}
    for user in sorted(users, key=lambda u: u.id):
        if user.external_id:
            by_external.setdefault((user.org_source, user.external_id), user)
        if user.email:
            by_email.setdefault(user.email.strip().lower(), user)
        by_login.setdefault(user.login.lower(), user)
    matched = {}
    for row in rows:
        user = (row.external_id and by_external.get((source, row.external_id))) \
            or (row.email and by_email.get(row.email)) \
            or (row.login and by_login.get(row.login.lower()))
        matched[row.line] = user or None
    return matched


def plan_import(rows, *, source="csv", create_missing=False, deactivate_missing=False, manager_refs=None, actor_id=None):
    """План импорта без записи в БД (см. модуль)."""
    plan = ImportPlan(source=source)
    snap = OrgSnapshot()
    users = list(snap.users.values())
    max_depth = org.get_settings()["maxDepth"]
    department_of = _resolve_departments(rows, plan, snap)
    matched = _match_users(rows, users, source)

    seen_emails, seen_users = {}, {}
    email_to_key, ref_to_key = {}, {}
    entries = []
    for row in rows:
        user = matched[row.line]
        errors = []
        if not row.email and not row.login:
            errors.append("нет email и логина")
        if row.email and row.email in seen_emails:
            errors.append(f"email повторяется (строка {seen_emails[row.email]})")
        if user is not None and user.id in seen_users:
            errors.append(f"сотрудник уже указан в строке {seen_users[user.id]}")
        if not row.department:
            errors.append("не указано подразделение")
        elif len(row.department) > max_depth:
            errors.append(f"в пути подразделения больше {max_depth} уровней — увеличьте глубину в настройках структуры")
        if user is None and not create_missing:
            errors.append("пользователь не найден (включите «создавать новых»)")
        if user is None and create_missing and not row.name:
            errors.append("для нового сотрудника нужно ФИО")
        if user is not None and row.disabled and user.is_active and user.id == actor_id:
            errors.append("свою учётную запись отключить нельзя")
        if row.email:
            seen_emails.setdefault(row.email, row.line)
        key = user.id if user is not None else f"new-user:{row.line}"
        if user is not None:
            seen_users.setdefault(user.id, row.line)
        if row.email:
            email_to_key.setdefault(row.email, key)
        if row.external_id:
            ref_to_key.setdefault(row.external_id, key)
        entries.append((row, user, key, errors))

    by_email_existing = {}
    for user in sorted(users, key=lambda u: u.id):
        if user.email:
            by_email_existing.setdefault(user.email.strip().lower(), user.id)
    refs = manager_refs or {}
    # Гипотетическая структура после импорта: проверка циклов и «явный ли руководитель»
    future = FutureOrg(snap)
    for item in plan.departments:
        future.add_unit(item["key"], item["parentKey"])
    wanted_manager, kept, by_file = {}, {}, set()
    for row, user, key, errors in entries:
        department_id = department_of.get(row.line)
        manager_key = None
        if row.manager_email:
            manager_key = email_to_key.get(row.manager_email) or by_email_existing.get(row.manager_email)
            if manager_key is None:
                errors.append(f"руководитель {row.manager_email} не найден")
        elif row.manager_ref:
            manager_key = ref_to_key.get(refs.get(row.manager_ref, row.manager_ref))
        elif user is not None and user.manager_id:
            # Руководитель в файле не указан — явное подчинение сохраняется как было
            kept[key] = user.manager_id
        if manager_key == key:
            errors.append("сотрудник указан своим руководителем")
            manager_key = None
        if manager_key is not None:
            by_file.add(key)
        wanted_manager[key] = manager_key
        base = future.people.get(key)
        name = row.name or (base.name if base else row.email or row.login)
        # Руководитель из файла снимает «первое лицо»; без руководителя в файле отметка сохраняется
        reports_to_none = bool(base and base.reports_to_none) and manager_key is None
        future.people[key] = FuturePerson(name, department_id, None, not row.disabled, reports_to_none)
        if row.is_head and department_id:
            future.add_head(department_id, key)
    # Явный руководитель — только там, где он отличается от руководителя по структуре
    explicit = {}
    for key, manager_key in wanted_manager.items():
        if manager_key is not None and future.structure_manager(key) != manager_key:
            explicit[key] = manager_key
    explicit.update(kept)
    for key, manager_key in explicit.items():
        future.people[key].manager_id = manager_key
    cyclic = future.cycles()
    plan.cycles = sorted({future.people[key].name for key in cyclic if key in future.people}, key=str.casefold)

    new_paths = {item["key"]: item["path"] for item in plan.departments}

    def label(department_id):
        if department_id in new_paths:
            return new_paths[department_id]
        return " / ".join(snap.path(department_id)) if department_id else ""

    def person_name(key):
        person = future.people.get(key) if key is not None else None
        return person.name if person else ""

    for row, user, key, errors in entries:
        if key in cyclic:
            errors.append("цикл подчинения: руководитель подчиняется сотруднику")
        department_id = department_of.get(row.line)
        target = {
            "department": department_id, "position": row.position, "manager": explicit.get(key),
            "head": bool(row.is_head and department_id), "name": row.name or None, "email": row.email,
            "login": row.login, "external_id": row.external_id, "disabled": row.disabled,
            "clear_reports_to_none": key in by_file,
        }
        plan.targets[key] = target
        changes = []
        if user is not None:
            if user.department_id != department_id:
                changes.append({"field": "department", "old": label(user.department_id), "new": label(department_id)})
            if row.position is not None and (user.position or None) != row.position:
                changes.append({"field": "position", "old": user.position or "", "new": row.position})
            if row.name and user.name != row.name:
                changes.append({"field": "name", "old": user.name, "new": row.name})
            if (user.manager_id or None) != target["manager"]:
                old = snap.users[user.manager_id].name if user.manager_id in snap.users else ""
                changes.append({"field": "manager", "old": old, "new": person_name(target["manager"])})
            if key in by_file and user.reports_to_none:
                changes.append({"field": "reportsToNone", "old": "да", "new": "нет"})
            if target["head"] and department_id not in snap.head_of.get(user.id, []):
                changes.append({"field": "head", "old": "", "new": label(department_id)})
            if row.disabled and user.is_active:
                changes.append({"field": "isActive", "old": "активен", "new": "отключён"})
        action = "error" if errors else "create" if user is None else "update" if changes else "unchanged"
        plan.errors += 1 if errors else 0
        plan.users.append({
            "line": row.line, "userId": user.id if user is not None else None,
            "name": row.name or (user.name if user else ""), "email": row.email or (user.email if user else ""),
            "login": row.login or (user.login if user else ""), "department": " / ".join(row.department),
            "manager": person_name(wanted_manager.get(key)) if wanted_manager.get(key) is not None else "",
            "managerExplicit": key in explicit, "action": action, "changes": changes, "errors": errors,
        })

    if deactivate_missing:
        present = {user.id for _row, user, _key, _errors in entries if user is not None}
        for user in sorted(users, key=lambda u: u.name.casefold()):
            # Свою учётную запись импорт не отключает — в системе всегда остаётся активный администратор
            if user.org_source == source and user.is_active and user.id not in present and user.id != actor_id:
                plan.deactivate.append({"userId": user.id, "name": user.name})
    return plan


# ---------------------------------------------------------------- применение
def apply_import(rows, *, actor_id, source="csv", create_missing=False, deactivate_missing=False, manager_refs=None):
    """(план, применён ли, id отключённых). При ошибках ничего не меняется."""
    plan = plan_import(rows, source=source, create_missing=create_missing, deactivate_missing=deactivate_missing,
                       manager_refs=manager_refs, actor_id=actor_id)
    if plan.errors:
        return plan.as_dict(), False, []
    comment = SOURCE_COMMENT.get(source, source)
    ids = {}
    for item in plan.departments:
        department = Department(id=org.new_id(), name=item["name"], parent_id=ids.get(item["parentKey"], item["parentKey"]),
                                kind=item["kind"], sort_order=0, org_source=source)
        db.session.add(department)
        ids[item["key"]] = department.id
        audit.record("Структура", "create", item["name"], "", "", f"{item['path']} ({comment})")
    db.session.flush()

    def real(key):
        return ids.get(key, key) if key is not None else None

    users_by_id = {user.id: user for user in db.session.execute(select(User)).scalars()}
    taken = {user.login.lower() for user in users_by_id.values()}
    created = set()
    for key, target in plan.targets.items():
        if not (isinstance(key, str) and key.startswith("new-user:")):
            continue
        user = User(
            login=unique_login(target["login"] or (target["email"] or "").split("@")[0], taken), name=target["name"],
            email=target["email"] or "", position=target["position"] or "", role="viewer",
            auth_source="ldap" if source == "ad" else "local", password_hash=None, is_active=not target["disabled"],
            org_source=source, external_id=target["external_id"] or None,
        )
        db.session.add(user)
        db.session.flush()
        ids[key] = user.id
        users_by_id[user.id] = user
        created.add(user.id)
        audit.record("Пользователи", "create", f"user #{user.id} ({user.name})", "", "", f"{user.name} ({comment})")

    snap = OrgSnapshot()
    primaries = {dep_id for dep_id, heads in snap.heads.items() if any(primary for _uid, primary in heads)}
    deactivated = []
    for key, target in plan.targets.items():
        user = users_by_id[real(key)]
        entity = f"user #{user.id} ({user.name})"
        changes = []
        department_id = real(target["department"])
        if user.department_id != department_id:
            changes.append(("department", " / ".join(snap.path(user.department_id)), " / ".join(snap.path(department_id))))
            user.department_id = department_id
        manager_id = real(target["manager"])
        if user.manager_id != manager_id:
            old = users_by_id[user.manager_id].name if user.manager_id in users_by_id else "по структуре"
            new = users_by_id[manager_id].name if manager_id in users_by_id else "по структуре"
            changes.append(("manager", old, new))
            user.manager_id = manager_id
        if target["clear_reports_to_none"] and user.reports_to_none:
            changes.append(("reportsToNone", "да", "нет"))
            user.reports_to_none = False
        if target["position"] is not None and user.position != target["position"]:
            changes.append(("position", user.position, target["position"]))
            user.position = target["position"]
        if target["name"] and user.name != target["name"]:
            changes.append(("name", user.name, target["name"]))
            user.name = target["name"]
        if target["external_id"] and not user.external_id:
            user.external_id = target["external_id"]
        if source != "csv":
            user.org_source = source
        if target["disabled"] and user.is_active:
            user.is_active = False
            changes.append(("is_active", "активен", "отключён"))
            deactivated.append(user.id)
        if target["head"] and department_id:
            exists = db.session.get(DepartmentHead, (department_id, user.id))
            if exists is None:
                primary = department_id not in primaries
                db.session.add(DepartmentHead(department_id=department_id, user_id=user.id, is_primary=primary))
                primaries.add(department_id)
                dep = db.session.get(Department, department_id)
                audit.record("Структура", "update", dep.name if dep else department_id, "heads", "", user.name)
        if user.id not in created:
            for attr, old, new in changes:
                audit.record("Пользователи", "update", entity, attr, old, f"{new} ({comment})")
    for item in plan.deactivate:
        user = users_by_id.get(item["userId"])
        if user is not None and user.is_active:
            user.is_active = False
            deactivated.append(user.id)
            audit.record("Пользователи", "update", f"user #{user.id} ({user.name})", "is_active", "активен",
                         f"отключён ({comment})")
    db.session.flush()
    return plan.as_dict(), True, deactivated
