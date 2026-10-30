"""Выгрузка и загрузка сотрудников файлом — формат ``podelu.employees`` версии 1, общий с
todo-planner «По Делу» (ADR-0008; ADR-0051 там).

Форматы: CSV (UTF-8 с BOM, «;», открывается в Excel), JSON и ZIP — ``employees.json`` и папка
``avatars/``. ZIP разбирает браузер: на сервер приходит только ``employees.json`` и SHA-256 фото,
сами фото загружаются отдельно после применения (``set_avatars``).

Загрузка — план без записи в БД, затем то же самое с решениями администратора одной транзакцией.

* Сотрудник файла сопоставляется с системой по email (без учёта регистра). Если по email не найден,
  а логин из файла принадлежит сотруднику без email — это он же; если сотруднику с другим email —
  администратор решает, тот же ли это человек («identity»). Запись без email ищется по логину.
* Коллизия — поле, у которого и в системе, и в файле есть разные значения: администратор выбирает
  значение системы, файла, объединение (руководимые подразделения) или вводит своё; по умолчанию
  остаётся значение системы. Пустое в системе заполняется из файла, пустое в файле ничего не стирает.
* Поля «По Делу», которых в «Пульсе» нет (функциональные руководители, полномочия, часовой пояс,
  видимость), выгружаются пустыми и при загрузке пропускаются с пометкой. Роль «По Делу»
  «Сотрудник» (user) оставляет роль системы, новому сотруднику — «Наблюдатель»; «Руководитель» —
  это «Менеджер».
* Свою роль и активность загрузкой не изменить; в системе всегда остаётся активный администратор.
* Пароли не выгружаются и не загружаются: новые сотрудники создаются без пароля.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app import audit
from app.api.avatars import sniff_image, store_avatar
from app.extensions import db
from app.models import Department, DepartmentHead, User, UserAvatar
from app.services import org
from app.services.org import OrgSnapshot, norm
from app.services.org_import import PATH_SPLIT, TRUE_VALUES, FuturePerson, FutureOrg, clean, detect_delimiter, unique_login
from app.version import APP_VERSION

FORMAT = "podelu.employees"
VERSION = 1
PRODUCT = "puls"
MAX_RECORDS = 10000
FALSE_VALUES = {"0", "false", "no", "нет", "n", "-"}
ROLES = ("admin", "manager", "editor", "viewer")
ROLE_LABELS = {"admin": "Администратор", "manager": "Менеджер", "editor": "Редактор", "viewer": "Наблюдатель"}
TODO_USER = "user"   # «Сотрудник» «По Делу»: роль системы не меняется, новому — «Наблюдатель»
ROLE_ALIASES = {
    **{code: code for code in ROLES}, **{label.lower(): code for code, label in ROLE_LABELS.items()},
    "руководитель": "manager", "user": TODO_USER, "сотрудник": TODO_USER, "пользователь": TODO_USER,
}
AUTH_ALIASES = {"local": "local", "пароль": "local", "ldap": "ldap", "домен": "ldap", "ad": "ldap"}
PRIMARY_MARK = "(основной)"
OWN_DEPARTMENT = "__own__"   # «Руководит: да» — своим подразделением
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Столбцы CSV при выгрузке — как у «По Делу»; лишние для «Пульса» остаются пустыми
CSV_COLUMNS = (
    ("email", "email"), ("login", "Логин"), ("name", "ФИО"), ("position", "Должность"),
    ("department", "Подразделение"), ("manager", "Руководитель (явно)"), ("reportsToNone", "Первое лицо"),
    ("heads", "Руководит"), ("functionalManagers", "Функциональные руководители"), ("role", "Роль"),
    ("capabilities", "Полномочия"), ("timezone", "Часовой пояс"), ("isActive", "Активен"),
    ("visibilityScope", "Видимость"), ("authSource", "Вход"), ("externalId", "Внешний ID"),
)
ALIASES = {
    "email": ("email", "e-mail", "mail", "почта", "эл. почта", "электронная почта"),
    "login": ("login", "логин", "samaccountname", "учётная запись", "учетная запись"),
    "name": ("name", "фио", "имя", "сотрудник", "displayname"),
    "position": ("position", "title", "должность"),
    "department": ("department", "department_path", "подразделение", "отдел", "путь подразделения"),
    "manager": ("manager", "manager_email", "руководитель", "руководитель (явно)", "email руководителя",
                "почта руководителя"),
    "reportsToNone": ("reportstonone", "reports_to_none", "первое лицо", "никому не подчиняется"),
    "heads": ("heads", "is_head", "head", "руководит", "руководит подразделениями", "руководитель подразделения"),
    "functionalManagers": ("functionalmanagers", "functional_managers", "функциональные руководители",
                           "функциональный руководитель"),
    "role": ("role", "global_role", "роль"),
    "capabilities": ("capabilities", "полномочия"),
    "timezone": ("timezone", "часовой пояс"),
    "isActive": ("isactive", "is_active", "active", "активен", "доступ"),
    "visibilityScope": ("visibilityscope", "visibility_scope", "видимость", "область видимости"),
    "authSource": ("authsource", "auth_source", "вход", "способ входа"),
    "externalId": ("externalid", "external_id", "внешний id", "табельный номер", "табельный", "objectguid"),
    "avatar": ("avatar", "фото", "аватар"),
}
FIELD_LABELS = {
    "name": "ФИО", "email": "Email", "login": "Логин", "position": "Должность", "department": "Подразделение",
    "manager": "Руководитель (явно)", "reportsToNone": "Первое лицо", "heads": "Руководит", "role": "Роль",
    "isActive": "Активен", "avatar": "Фото",
}
FIELD_KINDS = {
    "department": "path", "manager": "email", "reportsToNone": "bool", "heads": "list", "role": "role",
    "isActive": "bool", "avatar": "avatar",
}
LIST_FIELDS = {"heads"}
COMPARED = ("name", "email", "login", "position", "department", "manager", "reportsToNone", "heads", "role",
            "isActive", "avatar")
# Поля «По Делу», которых в «Пульсе» нет
FOREIGN_FIELDS = {
    "functionalManagers": "функциональные руководители", "capabilities": "полномочия",
    "timezone": "часовой пояс", "visibilityScope": "видимость",
}
AVATAR_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


class EmployeeFormatError(ValueError):
    """Файл не разобран: не тот формат, нет нужных столбцов, слишком большой."""


@dataclass
class Record:
    line: int
    email: str = ""
    login: str = ""
    name: str = ""
    position: str | None = None
    department: tuple | None = None
    manager: str | None = None            # email руководителя (или логин, если email у него нет)
    reports_to_none: bool | None = None
    heads: tuple | None = None            # ((путь, основной), ...)
    role: str | None = None
    is_active: bool | None = None
    auth_source: str | None = None
    external_id: str | None = None
    avatar: str | None = None
    foreign: set = field(default_factory=set)
    notes: list = field(default_factory=list)   # что из записи не применится и почему


def record_key(record):
    """Ключ записи в плане и в решениях: email, а без него — логин."""
    if record.email:
        return record.email
    if record.login:
        return f"login:{record.login.lower()}"
    return f"line:{record.line}"


# ---------------------------------------------------------------- разбор значений
def _path(value):
    if isinstance(value, (list, tuple)):
        parts = [clean(part) for part in value]
    else:
        parts = [clean(part) for part in PATH_SPLIT.split(str(value or "").strip())]
    parts = tuple(part[:200] for part in parts if part)
    return parts or None


def path_text(path):
    return " / ".join(path or ())


def _bool(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def _filled(value):
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return str(value or "").strip() != ""


def _heads(value):
    """«Руководит»: «да» — своим подразделением; иначе пути через «|», «(основной)» — основной."""
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                path = _path(item.get("department") or item.get("path"))
                if path:
                    items.append((path, bool(item.get("primary"))))
            else:
                items.extend(_heads(str(item)) or ())
        return tuple(items) or None
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower() in TRUE_VALUES:
        return ((OWN_DEPARTMENT, True),)
    if text.lower() in FALSE_VALUES:
        return None
    items = []
    for part in text.split("|"):
        primary = PRIMARY_MARK in part.lower()
        path = _path(re.sub(re.escape(PRIMARY_MARK), "", part, flags=re.IGNORECASE))
        if path:
            items.append((path, primary))
    return tuple(items) or None


def _record(line, raw):
    """Словарь (из CSV или JSON) → Record; непонятные значения — в notes."""
    record = Record(line=line)
    record.email = str(raw.get("email") or "").strip().lower()
    record.login = str(raw.get("login") or "").strip()
    record.name = clean(raw.get("name"))[:255]
    record.position = clean(raw.get("position"))[:500] or None
    record.department = _path(raw.get("department"))
    record.manager = str(raw.get("manager") or "").strip().lower() or None
    if raw.get("reportsToNone") not in (None, ""):
        record.reports_to_none = _bool(raw.get("reportsToNone"))
    record.heads = _heads(raw.get("heads"))
    role = str(raw.get("role") or "").strip().lower()
    if role:
        record.role = ROLE_ALIASES.get(role)
        if record.role is None:
            record.notes.append(f"роль «{raw.get('role')}» не распознана — не применяется")
    if raw.get("isActive") not in (None, ""):
        record.is_active = _bool(raw.get("isActive"))
    auth = str(raw.get("authSource") or "").strip().lower()
    if auth:
        record.auth_source = AUTH_ALIASES.get(auth)
        if record.auth_source is None:
            record.notes.append(f"способ входа «{raw.get('authSource')}» не распознан — не применяется")
    record.external_id = str(raw.get("externalId") or "").strip()[:255] or None
    record.avatar = str(raw.get("avatar") or "").strip() or None
    record.foreign = {key for key in FOREIGN_FIELDS if _filled(raw.get(key))}
    return record


def parse_csv(text):
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        raise EmployeeFormatError("Файл пустой")
    reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(text))
    header = next(reader, None) or []
    mapping = {}
    for index, title in enumerate(header):
        key = (title or "").strip().lower().lstrip("\ufeff")
        for column, aliases in ALIASES.items():
            if key in aliases and column not in mapping:
                mapping[column] = index
    if "email" not in mapping and "login" not in mapping:
        raise EmployeeFormatError("Нужен столбец email (или «Логин»)")
    records = []
    for line, values in enumerate(reader, start=2):
        if not any((value or "").strip() for value in values):
            continue
        if len(records) >= MAX_RECORDS:
            raise EmployeeFormatError(f"Не больше {MAX_RECORDS} сотрудников за одну загрузку")
        raw = {column: (values[index] if index < len(values) else "") for column, index in mapping.items()}
        records.append(_record(line, raw))
    return records


def parse_json(content):
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError as error:
            raise EmployeeFormatError("Файл не является JSON") from error
    if isinstance(content, dict):
        if content.get("format") != FORMAT:
            raise EmployeeFormatError("Это не файл сотрудников (format: podelu.employees)")
        if content.get("version") != VERSION:
            raise EmployeeFormatError(f"Версия файла {content.get('version')} не поддерживается — нужна {VERSION}")
        items = content.get("employees")
    else:
        items = content
    if not isinstance(items, list):
        raise EmployeeFormatError("В файле нет списка сотрудников")
    if len(items) > MAX_RECORDS:
        raise EmployeeFormatError(f"Не больше {MAX_RECORDS} сотрудников за одну загрузку")
    return [_record(index, item) for index, item in enumerate(items, start=1) if isinstance(item, dict)]


def parse_content(fmt, content):
    if fmt == "csv":
        return parse_csv(content)
    if fmt == "json":
        return parse_json(content)
    raise EmployeeFormatError("Формат: csv или json")


# ---------------------------------------------------------------- выгрузка
def _manager_ref(user):
    """Ссылка на сотрудника в файле: email, а если его нет — логин."""
    return (user.email or "").strip() or user.login


def export_records(*, include_inactive=True, include_without_email=True):
    """Сотрудники для выгрузки. «По Делу» загружает только сотрудников с email: для файла туда
    сотрудников без email можно не выгружать (include_without_email=False)."""
    snap = OrgSnapshot()
    records = []
    for user in sorted(snap.users.values(), key=lambda u: (u.name.casefold(), u.id)):
        if not include_inactive and not user.is_active:
            continue
        if not include_without_email and not (user.email or "").strip():
            continue
        manager = snap.users.get(user.manager_id) if user.manager_id else None
        heads = []
        for dep_id in snap.head_of.get(user.id, []):
            primary = next((p for uid, p in snap.heads.get(dep_id, []) if uid == user.id), False)
            heads.append({"department": snap.path(dep_id), "primary": bool(primary)})
        heads.sort(key=lambda item: (not item["primary"], path_text(item["department"]).casefold()))
        records.append({
            "email": (user.email or "").strip(), "login": user.login, "name": user.name, "position": user.position or "",
            "department": snap.path(user.department_id) if user.department_id in snap.departments else [],
            "manager": _manager_ref(manager) if manager is not None else "",
            "reportsToNone": bool(user.reports_to_none), "heads": heads,
            "functionalManagers": [], "role": user.role, "capabilities": [], "timezone": "",
            "isActive": bool(user.is_active), "visibilityScope": "", "authSource": user.auth_source or "local",
            "externalId": user.external_id or "",
            "_userId": user.id, "_hasAvatar": bool(user.avatar_version),
        })
    return records


def _public(record):
    return {key: value for key, value in record.items() if not key.startswith("_")}


def export_document(records):
    return {
        "format": FORMAT, "version": VERSION,
        "exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "app": APP_VERSION, "product": PRODUCT,
        "employees": [_public(record) for record in records],
    }


def _csv_value(key, value):
    if key == "department":
        return path_text(value)
    if key == "heads":
        return " | ".join(path_text(item["department"]) + (f" {PRIMARY_MARK}" if item["primary"] else "") for item in value)
    if key in ("functionalManagers", "capabilities"):
        return ", ".join(value)
    if key == "role":
        return ROLE_LABELS.get(value, value)
    if key in ("reportsToNone", "isActive"):
        return "да" if value else "нет"
    return value or ""


def export_csv(records):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([header for _key, header in CSV_COLUMNS])
    for record in records:
        writer.writerow([_csv_value(key, record.get(key)) for key, _header in CSV_COLUMNS])
    return "\ufeff" + buffer.getvalue()   # BOM — Excel открывает UTF-8 без вопросов


README = (
    "Выгрузка сотрудников «Пульса Проекта» (формат podelu.employees, общий с «По Делу»).\r\n"
    "employees.json — сотрудники; avatars/ — фото (если выгружались).\r\n"
    "Загрузка: Администрирование → Импорт и экспорт → Сотрудники → Выбрать файл (этот архив целиком).\r\n"
    "Пароли не выгружаются.\r\n"
)


def export_zip(records, *, with_avatars):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if with_avatars:
            ids = [record["_userId"] for record in records if record["_hasAvatar"]]
            avatars = {}
            for start in range(0, len(ids), 200):
                rows = db.session.execute(select(UserAvatar.user_id, UserAvatar.data, UserAvatar.content_type)
                                          .where(UserAvatar.user_id.in_(ids[start:start + 200])))
                for user_id, data, content_type in rows:
                    avatars[user_id] = (data, content_type)
            for index, record in enumerate(records, start=1):
                data, content_type = avatars.get(record["_userId"], (None, None))
                if not data:
                    continue
                name = f"avatars/{index}.{AVATAR_EXT.get(content_type or '', 'png')}"
                # Фото уже сжаты — без повторного сжатия
                archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), bytes(data),
                                 compress_type=zipfile.ZIP_STORED)
                record["avatar"] = name
        archive.writestr("employees.json", json.dumps(export_document(records), ensure_ascii=False, indent=2))
        archive.writestr("README.txt", README)
    return buffer.getvalue()


# ---------------------------------------------------------------- план загрузки
class _Paths:
    """Пути подразделений → id существующего или ключ нового ("new:<n>")."""

    def __init__(self, snap):
        self.children = {(dep.parent_id, norm(dep.name)): dep.id for dep in snap.departments.values()}
        self.has_roots = any(dep.parent_id is None for dep in snap.departments.values())
        self.kinds = [kind["value"] for kind in org.get_settings()["unitKinds"]]
        self.created = []

    def resolve(self, path):
        if not path:
            return None
        parent, walked = None, []
        for depth, name in enumerate(path):
            walked.append(name)
            key = (parent, norm(name))
            if key not in self.children:
                new_key = f"new:{len(self.created) + 1}"
                kind = "company" if depth == 0 and not self.has_roots else "department"
                self.created.append({"key": new_key, "path": path_text(walked), "name": name, "parentKey": parent,
                                     "kind": kind if kind in self.kinds else self.kinds[0]})
                self.children[key] = new_key
            parent = self.children[key]
        return parent


def _same(field_name, left, right):
    if field_name == "avatar":
        return isinstance(left, str) and isinstance(right, str) and left == right and left not in ("", "file")
    if field_name == "department":
        return tuple(norm(part) for part in left or ()) == tuple(norm(part) for part in right or ())
    if field_name == "heads":
        return {(tuple(norm(p) for p in path), primary) for path, primary in left or ()} == \
               {(tuple(norm(p) for p in path), primary) for path, primary in right or ()}
    if field_name in ("email", "manager", "login"):
        return (left or "").lower() == (right or "").lower()
    return (left if left != "" else None) == (right if right != "" else None)


def _empty(value):
    return value is None or value == "" or value == () or value == []


def _text(field_name, value):
    """Значение для показа администратору."""
    if _empty(value):
        return ""
    if field_name == "department":
        return path_text(value)
    if field_name == "heads":
        return [path_text(path) + (f" {PRIMARY_MARK}" if primary else "") for path, primary in value]
    if field_name == "role":
        return ROLE_LABELS.get(value, value)
    if field_name in ("reportsToNone", "isActive"):
        return "да" if value else "нет"
    if field_name == "avatar":
        return "есть" if value else ""
    return value


def _merge(field_name, current, incoming):
    merged = {}
    for path, primary in (*(current or ()), *(incoming or ())):
        key = tuple(norm(p) for p in path)
        previous = merged.get(key)
        merged[key] = (path, primary or (previous[1] if previous else False))
    return tuple(merged.values())


def _custom(field_name, value):
    """Своё значение администратора → (значение, ошибка)."""
    if field_name in ("name", "position", "email", "login", "manager"):
        text = clean(value)
        if field_name == "name" and not text:
            return None, "ФИО не может быть пустым"
        if field_name == "email" and not EMAIL.match(text):
            return None, "некорректный email"
        if field_name == "login" and not re.match(r"^\S{1,150}$", text):
            return None, "логин: без пробелов, до 150 символов"
        if field_name in ("email", "manager"):
            text = text.lower()
        return text or None, None
    if field_name == "department":
        return _path(value), None
    if field_name in ("reportsToNone", "isActive"):
        result = _bool(value)
        return (result, None) if result is not None else (None, "ожидается да или нет")
    if field_name == "heads":
        return _heads(value if isinstance(value, list) else str(value or "")) or (), None
    if field_name == "role":
        role = ROLE_ALIASES.get(str(value or "").strip().lower())
        return (role, None) if role in ROLES else (None, "роль: Администратор, Менеджер, Редактор или Наблюдатель")
    return None, "это поле нельзя ввести вручную"


def _own(value, own_department):
    """«Руководит: да» → своё подразделение."""
    value = tuple((own_department or (), primary) if path == OWN_DEPARTMENT else (path, primary)
                  for path, primary in value or ())
    return tuple(item for item in value if item[0])


def _incoming(record, field_name, own_department):
    value = {
        "name": record.name or None, "email": record.email or None, "login": record.login or None,
        "position": record.position, "department": record.department, "manager": record.manager,
        "reportsToNone": record.reports_to_none, "heads": record.heads,
        "role": record.role if record.role in ROLES else None, "isActive": record.is_active,
        "avatar": True if record.avatar else None,
    }[field_name]
    if field_name == "heads" and value:
        value = _own(value, own_department)
    return value


@dataclass
class _Entry:
    record: Record
    user: User | None
    key: str
    matched: str | None = None    # email | login | identity
    action: str = "create"
    final: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    identity: dict | None = None
    unresolved: bool = False
    touched: set = field(default_factory=set)   # поля, которые меняются


@dataclass
class EmployeePlan:
    entries: list = field(default_factory=list)
    paths: _Paths | None = None
    notes: list = field(default_factory=list)

    def as_dict(self):
        actions = [entry.action for entry in self.entries]
        errors = sum(1 for entry in self.entries if entry.errors)
        unresolved = sum(1 for entry in self.entries if entry.unresolved)
        return {
            "summary": {
                "records": len(self.entries),
                "create": actions.count("create"),
                "update": actions.count("update"),
                "unchanged": actions.count("unchanged"),
                "skip": actions.count("skip"),
                "conflicts": sum(len(entry.conflicts) for entry in self.entries),
                "unresolved": unresolved,
                "errors": errors,
                "createDepartments": len(self.paths.created) if self.paths else 0,
                "avatars": sum(1 for entry in self.entries if entry.final.get("_avatar") and not entry.errors),
            },
            "departments": [{"key": item["key"], "path": item["path"]} for item in (self.paths.created if self.paths else [])],
            "records": [{
                "key": entry.key, "line": entry.record.line, "action": entry.action,
                "userId": entry.user.id if entry.user is not None else None,
                "name": entry.final.get("name") or entry.record.name or (entry.user.name if entry.user else ""),
                "email": entry.final.get("email") or entry.record.email or ((entry.user.email or "") if entry.user else ""),
                "login": entry.final.get("login") or entry.record.login or (entry.user.login if entry.user else ""),
                "identity": entry.identity, "unresolved": entry.unresolved,
                "changes": entry.changes, "conflicts": entry.conflicts,
                "errors": entry.errors, "warnings": entry.warnings,
                "avatar": bool(entry.final.get("_avatar")),
            } for entry in self.entries],
            "notes": self.notes,
            "canApply": not errors and not unresolved,
        }


def _current_values(user, snap, avatar_hashes):
    manager = snap.users.get(user.manager_id) if user.manager_id else None
    heads = []
    for dep_id in snap.head_of.get(user.id, []):
        primary = next((p for uid, p in snap.heads.get(dep_id, []) if uid == user.id), False)
        heads.append((tuple(snap.path(dep_id)), bool(primary)))
    return {
        "name": user.name, "email": (user.email or "").strip().lower() or None, "login": user.login,
        "position": user.position or None,
        "department": tuple(snap.path(user.department_id)) or None if user.department_id in snap.departments else None,
        "manager": _manager_ref(manager).lower() if manager is not None else None,
        "reportsToNone": bool(user.reports_to_none), "heads": tuple(heads) or None,
        "role": user.role, "isActive": bool(user.is_active), "avatar": avatar_hashes.get(user.id),
    }


def plan_employees(records, *, actor_id, new_users="active", decisions=None, avatars=None):
    """План загрузки. decisions — {ключ записи: {"identity": "same"|"new",
    "fields": {поле: {"choice": "current"|"incoming"|"merge"|"custom", "value": ...}}}};
    avatars — фото в архиве: {ключ записи: SHA-256 файла} (или список ключей без хешей)."""
    decisions = decisions if isinstance(decisions, dict) else {}
    if isinstance(avatars, dict):
        avatars = {str(key).lower(): str(digest or "") for key, digest in avatars.items()}
    else:
        avatars = {str(key).lower(): "" for key in (avatars or ())}
    plan = EmployeePlan()
    snap = OrgSnapshot()
    users = sorted(snap.users.values(), key=lambda u: u.id)
    avatar_hashes = dict(db.session.execute(select(UserAvatar.user_id, UserAvatar.sha256)).all())
    by_email, by_login = {}, {}
    for user in users:
        if (user.email or "").strip():
            by_email.setdefault(user.email.strip().lower(), []).append(user)
        by_login.setdefault(user.login.lower(), user)
    plan.paths = _Paths(snap)
    foreign = set().union(*(record.foreign for record in records)) if records else set()
    if foreign:
        plan.notes.append("Поля «По Делу», которых нет в «Пульсе», не загружаются: "
                          + ", ".join(FOREIGN_FIELDS[key] for key in FOREIGN_FIELDS if key in foreign))

    seen_emails, seen_logins, seen_users = {}, {}, {}
    for record in records:
        key = record_key(record)
        entry = _Entry(record=record, user=None, key=key)
        entry.warnings.extend(record.notes)
        plan.entries.append(entry)
        if not record.email and not record.login:
            entry.errors.append("нет email и логина")
            continue
        if record.email and not EMAIL.match(record.email):
            entry.errors.append("некорректный email")
            continue
        if record.email and record.email in seen_emails:
            entry.errors.append(f"email повторяется (строка {seen_emails[record.email]})")
            continue
        if not record.email and record.login.lower() in seen_logins:
            entry.errors.append(f"логин «{record.login}» повторяется (строка {seen_logins[record.login.lower()]})")
            continue
        if record.email:
            seen_emails[record.email] = record.line
        if record.login:
            seen_logins.setdefault(record.login.lower(), record.line)
        decision = decisions.get(key) if isinstance(decisions.get(key), dict) else {}
        user = None
        if record.email:
            matches = by_email.get(record.email, [])
            if len(matches) > 1:
                entry.errors.append("в системе несколько сотрудников с таким email — исправьте в системе")
                continue
            user = matches[0] if matches else None
            entry.matched = "email" if user is not None else None
            if user is None and record.login and record.login.lower() in by_login:
                other = by_login[record.login.lower()]
                if not (other.email or "").strip():
                    # Логин принадлежит сотруднику без email — это он же
                    user, entry.matched = other, "login"
                else:
                    entry.identity = {"userId": other.id, "name": other.name, "email": other.email, "login": other.login}
                    choice = decision.get("identity")
                    if choice == "same":
                        user, entry.matched = other, "identity"
                    elif choice != "new":
                        entry.unresolved = True
        else:
            user = by_login.get(record.login.lower())
            entry.matched = "login" if user is not None else None
        if user is not None and user.id in seen_users:
            entry.errors.append(f"сотрудник уже указан в строке {seen_users[user.id]}")
            continue
        if user is not None:
            seen_users[user.id] = record.line
        entry.user = user
        _plan_entry(entry, decision, actor_id=actor_id, new_users=new_users, snap=snap, by_login=by_login,
                    avatar_hashes=avatar_hashes, avatar_in_file=avatars.get(key) if key in avatars else None)
    _check_logins(plan, by_login)
    _resolve_people(plan, snap, by_email, by_login)
    _check_admins(plan, snap)
    return plan


def _plan_entry(entry, decision, *, actor_id, new_users, snap, by_login, avatar_hashes, avatar_in_file):
    record, user = entry.record, entry.user
    field_decisions = decision.get("fields") if isinstance(decision.get("fields"), dict) else {}
    if user is None:
        # Новый сотрудник
        if not record.name:
            entry.errors.append("для нового сотрудника нужно ФИО")
        active = {"active": True, "inactive": False}.get(new_users, record.is_active if record.is_active is not None else True)
        login = record.login or None
        if login and len(login) > 150:
            entry.errors.append("логин длиннее 150 символов")
        login_decision = field_decisions.get("login") if isinstance(field_decisions.get("login"), dict) else {}
        if entry.identity is not None and decision.get("identity") == "new":
            # Логин из файла занят другим сотрудником — нужен другой
            custom, error = _custom("login", login_decision.get("value")) if login_decision.get("choice") == "custom" \
                else (None, None)
            entry.conflicts.append({
                "field": "login", "label": FIELD_LABELS["login"], "kind": "text", "current": "",
                "incoming": record.login, "choices": ["custom"], "choice": "custom",
                "value": login_decision.get("value") or "",
                "note": f"логин «{record.login}» занят сотрудником {entry.identity['name']}",
            })
            if error or not custom:
                entry.errors.append(error or f"логин «{record.login}» занят — укажите другой")
            login = custom
        entry.final = {
            "name": record.name, "email": record.email or None, "login": login, "position": record.position,
            "department": record.department, "manager": record.manager,
            "reportsToNone": bool(record.reports_to_none), "heads": _own(record.heads, record.department),
            "role": record.role if record.role in ROLES else "viewer", "isActive": active,
            "authSource": record.auth_source or "local", "externalId": record.external_id,
            "_avatar": avatar_in_file is not None,
        }
        entry.touched = set(entry.final)
        entry.action = "create"
        return

    current = _current_values(user, snap, avatar_hashes)
    final = dict(current)
    own = record.department or current["department"]
    for field_name in COMPARED:
        incoming = _incoming(record, field_name, own)
        if field_name == "avatar":
            # Хеш фото из файла; без хеша (или без фото в системе) сравнить нельзя
            incoming = (avatar_in_file or "file") if avatar_in_file is not None else None
        if _empty(incoming):
            continue
        if field_name == "email" and entry.matched == "email":
            continue   # сопоставлен по email — совпадает
        if field_name in ("role", "isActive") and user.id == actor_id and not _same(field_name, current[field_name], incoming):
            entry.warnings.append(f"{FIELD_LABELS[field_name]}: свою учётную запись изменить нельзя — не применяется")
            continue
        if _same(field_name, current[field_name], incoming):
            continue
        if _empty(current[field_name]) and field_name not in ("reportsToNone", "isActive"):
            # В системе пусто — берём из файла без вопросов
            final[field_name] = incoming
            entry.touched.add(field_name)
            entry.changes.append({"field": field_name, "label": FIELD_LABELS[field_name], "old": "",
                                  "new": _text(field_name, incoming)})
            continue
        choices = ["current", "incoming", "custom"]
        if field_name in LIST_FIELDS:
            choices.insert(2, "merge")
        if field_name == "avatar":
            choices = ["current", "incoming"]
        note = None
        if field_name == "login":
            holder = by_login.get(incoming.lower())
            if holder is not None and holder.id != user.id:
                choices = ["current", "custom"]
                note = f"логин «{incoming}» занят сотрудником {holder.name}"
        chosen = field_decisions.get(field_name) if isinstance(field_decisions.get(field_name), dict) else {}
        choice = chosen.get("choice") if chosen.get("choice") in choices else "current"
        value = current[field_name]
        if choice == "incoming":
            value = incoming
        elif choice == "merge":
            value = _merge(field_name, current[field_name], incoming)
        elif choice == "custom":
            value, error = _custom(field_name, chosen.get("value"))
            if error:
                entry.errors.append(f"{FIELD_LABELS[field_name]}: {error}")
                value = current[field_name]
            elif field_name == "heads":
                value = _own(value, own)
        if choice != "current" and not _same(field_name, current[field_name], value):
            final[field_name] = value
            entry.touched.add(field_name)
        entry.conflicts.append({
            "field": field_name, "label": FIELD_LABELS[field_name], "kind": FIELD_KINDS.get(field_name, "text"),
            "current": _text(field_name, current[field_name]), "incoming": _text(field_name, incoming),
            "merged": _text(field_name, _merge(field_name, current[field_name], incoming)) if field_name in LIST_FIELDS else None,
            "choices": choices, "choice": choice,
            "value": _text(field_name, value) if choice == "custom" else None,
            "note": note,
        })
    if record.external_id and not user.external_id:
        final["externalId"] = record.external_id
        entry.touched.add("externalId")
    final["_avatar"] = "avatar" in entry.touched
    entry.final = final
    # Коллизии, оставленные как в системе, ничего не меняют; запись с ними показывается всё равно
    entry.action = "update" if entry.touched else "unchanged"


def _check_logins(plan, by_login):
    """Логины после загрузки не повторяются."""
    taken = {}
    for entry in plan.entries:
        if entry.errors or entry.action == "skip":
            continue
        login = entry.final.get("login")
        if not login:
            continue
        holder = by_login.get(login.lower())
        if holder is not None and (entry.user is None or holder.id != entry.user.id):
            if entry.user is None and entry.identity is None:
                entry.errors.append(f"логин «{login}» занят сотрудником {holder.name} — уберите логин из файла или измените")
            elif entry.user is not None:
                entry.errors.append(f"логин «{login}» занят сотрудником {holder.name}")
        if login.lower() in taken:
            entry.errors.append(f"логин «{login}» повторяется (строка {taken[login.lower()]})")
        taken.setdefault(login.lower(), entry.record.line)


def _person_key(entry):
    return entry.user.id if entry.user is not None else f"new-user:{entry.key}"


def _resolve_people(plan, snap, by_email, by_login):
    """Подразделения, руководители и проверка циклов подчинения по итоговым значениям."""
    max_depth = org.get_settings()["maxDepth"]
    active = [entry for entry in plan.entries if entry.final and not entry.errors and entry.action != "skip"]
    emails, logins = {}, {}
    for entry in active:
        if entry.final.get("email"):
            emails[entry.final["email"].lower()] = _person_key(entry)
        if entry.final.get("login"):
            logins[entry.final["login"].lower()] = _person_key(entry)
    for email, users in by_email.items():
        if len(users) == 1:
            emails.setdefault(email, users[0].id)
    for login, user in by_login.items():
        logins.setdefault(login, user.id)

    for entry in active:
        final = entry.final
        for path in [final.get("department"), *(path for path, _primary in final.get("heads") or ())]:
            if path and len(path) > max_depth:
                entry.errors.append(f"в пути «{path_text(path)}» больше {max_depth} уровней — увеличьте глубину "
                                    "в настройках структуры")
        final["_department"] = plan.paths.resolve(final.get("department")) if final.get("department") else None
        final["_heads"] = []
        for path, primary in final.get("heads") or ():
            department = plan.paths.resolve(path)
            if department:
                final["_heads"].append((department, primary))
        manager = final.get("manager")
        final["_manager"] = None
        if manager:
            final["_manager"] = (emails if "@" in manager else logins).get(manager.lower())
            if final["_manager"] is None:
                entry.warnings.append(f"руководитель {manager} не найден ни в системе, ни в файле — не назначается")
            elif final["_manager"] == _person_key(entry):
                entry.warnings.append("сотрудник указан своим руководителем — не назначается")
                final["_manager"] = None
            if final["_manager"] is None:
                final["manager"] = None
                entry.touched.discard("manager")
    future = FutureOrg(snap)
    for item in plan.paths.created:
        future.add_unit(item["key"], item["parentKey"])
    active = [entry for entry in active if not entry.errors]
    for entry in active:
        final = entry.final
        key = final["_key"] = _person_key(entry)
        base = future.people.get(key)
        # Активность не влияет на подчинение: руководитель по структуре вычисляется как для активных
        future.people[key] = FuturePerson(final.get("name") or (base.name if base else key), final["_department"],
                                          None, True, bool(final.get("reportsToNone")))
        if "heads" in entry.touched or entry.user is None:
            future.clear_heads(key)
            for department_id, primary in final["_heads"]:
                future.add_head(department_id, key, primary)
    # Явный руководитель — только если он отличается от руководителя по структуре
    for entry in active:
        final = entry.final
        key = final["_key"]
        manager = final["_manager"]
        if "manager" in entry.touched or entry.user is None:
            if manager is not None and future.structure_manager(key) == manager:
                manager = None
            final["_explicit"] = manager
        else:
            final["_explicit"] = entry.user.manager_id
        if entry.user is not None and "manager" in entry.touched and final["_explicit"] == entry.user.manager_id:
            # Руководитель из файла — тот же, что по структуре: явный не нужен, менять нечего
            entry.touched.discard("manager")
            entry.changes = [change for change in entry.changes if change["field"] != "manager"]
            entry.action = "update" if entry.touched else "unchanged"
        future.people[key].manager_id = final["_explicit"]
        if final["_explicit"] is not None and "reportsToNone" not in entry.touched:
            future.people[key].reports_to_none = False   # явный руководитель снимает «первое лицо»
    cyclic = future.cycles()
    for entry in active:
        if entry.final["_key"] in cyclic:
            entry.errors.append("цикл подчинения: руководитель подчиняется сотруднику")


def _check_admins(plan, snap):
    """В системе остаётся хотя бы один активный администратор."""
    final = {entry.user.id: entry.final for entry in plan.entries
             if entry.user is not None and entry.final and not entry.errors and entry.action == "update"}
    admins = 0
    for user in snap.users.values():
        values = final.get(user.id, {})
        if values.get("role", user.role) == "admin" and values.get("isActive", bool(user.is_active)):
            admins += 1
    admins += sum(1 for entry in plan.entries if entry.user is None and entry.final and not entry.errors
                  and entry.final.get("role") == "admin" and entry.final.get("isActive"))
    if admins:
        return
    for entry in plan.entries:
        if entry.user is not None and entry.user.role == "admin" and entry.user.is_active \
                and ({"role", "isActive"} & entry.touched):
            entry.errors.append("в системе должен остаться хотя бы один активный администратор")


# ---------------------------------------------------------------- применение
def apply_employees(records, *, actor_id, new_users="active", decisions=None, avatars=None):
    """(план, применён ли, {ключ записи: id}, [ключи, чьи фото загрузить], [id отключённых]).
    При ошибках ничего не меняется."""
    plan = plan_employees(records, actor_id=actor_id, new_users=new_users, decisions=decisions, avatars=avatars)
    result = plan.as_dict()
    if not result["canApply"]:
        return result, False, {}, [], []
    ids = {}
    for item in plan.paths.created:
        department = Department(id=org.new_id(), name=item["name"], parent_id=ids.get(item["parentKey"], item["parentKey"]),
                                kind=item["kind"], sort_order=0, org_source="import")
        db.session.add(department)
        ids[item["key"]] = department.id
        audit.record("Структура", "create", item["name"], "", "", f"{item['path']} (загрузка сотрудников)")
    db.session.flush()

    def real(key):
        return ids.get(key, key) if key is not None else None

    taken = {login.lower() for login in db.session.execute(select(User.login)).scalars()}
    active = [entry for entry in plan.entries if entry.final and entry.action in ("create", "update")]
    for entry in active:
        if entry.user is not None:
            continue
        final = entry.final
        login = final.get("login")
        if login:
            taken.add(login.lower())
        else:
            login = unique_login((final.get("email") or "").split("@")[0] or "user", taken)
        user = User(
            login=login, name=final["name"], email=final.get("email") or "", position=final.get("position") or "",
            role=final.get("role") or "viewer", is_active=bool(final.get("isActive", True)), password_hash=None,
            auth_source=final.get("authSource") or "local", reports_to_none=bool(final.get("reportsToNone")),
            org_source="import", external_id=final.get("externalId"),
        )
        db.session.add(user)
        db.session.flush()
        entry.user = user
        ids[final["_key"]] = user.id
        audit.record("Пользователи", "create", f"user #{user.id} ({user.name})", "", "", f"{user.name} (загрузка сотрудников)")

    snap = OrgSnapshot()
    names = {user_id: user.name for user_id, user in snap.users.items()}
    primaries = {dep_id for dep_id, heads in snap.heads.items() if any(primary for _uid, primary in heads)}
    deactivated = []
    for entry in active:
        user, final, created = entry.user, entry.final, entry.action == "create"
        entity = f"user #{user.id} ({user.name})"
        changes = []

        def change(attr, label, new, user=user, changes=changes):
            old = getattr(user, attr)
            if old != new:
                setattr(user, attr, new)
                changes.append((label, old, new))

        touched = entry.touched
        if not created:
            for field_name, attr in (("name", "name"), ("login", "login"), ("position", "position"),
                                     ("role", "role"), ("isActive", "is_active"), ("reportsToNone", "reports_to_none")):
                if field_name in touched:
                    value = final.get(field_name)
                    change(attr, attr, value if value is not None or attr != "position" else "")
            if "email" in touched:
                change("email", "email", final.get("email") or "")
            if "externalId" in touched and not user.external_id:
                user.external_id = final.get("externalId")
            if "isActive" in touched and not user.is_active:
                deactivated.append(user.id)
        if "department" in touched or created:
            department_id = real(final.get("_department"))
            old = " / ".join(snap.path(user.department_id))
            if user.department_id != department_id:
                user.department_id = department_id
                changes.append(("department", old, final_path(final)))
        if "manager" in touched or created:
            manager_id = real(final.get("_explicit"))
            if user.manager_id != manager_id:
                changes.append(("manager", names.get(user.manager_id, "по структуре"),
                                names.get(manager_id, "по структуре")))
                user.manager_id = manager_id
            if user.manager_id and "reportsToNone" not in touched:
                user.reports_to_none = False   # явный руководитель снимает «первое лицо» (как в карточке)
        if user.manager_id and user.reports_to_none:
            user.manager_id = None
        if "heads" in touched or created:
            wanted = {}
            for department, primary in final.get("_heads") or []:
                wanted.setdefault(real(department), primary)
            existing = {row.department_id: row for row in db.session.execute(
                select(DepartmentHead).where(DepartmentHead.user_id == user.id)).scalars()}
            for department_id, row in existing.items():
                if department_id not in wanted:
                    if row.is_primary:
                        primaries.discard(department_id)
                    db.session.delete(row)
                    audit.record("Структура", "update", " / ".join(snap.path(department_id)), "heads", user.name, "")
            db.session.flush()
            for department_id, primary in wanted.items():
                if department_id in existing:
                    continue
                is_primary = bool(primary) and department_id not in primaries
                db.session.add(DepartmentHead(department_id=department_id, user_id=user.id, is_primary=is_primary))
                if is_primary:
                    primaries.add(department_id)
                audit.record("Структура", "update", _dep_name(department_id), "heads", "", user.name)
        names[user.id] = user.name
        if not created:
            for attr, old, new in changes:
                if attr == "is_active":
                    old, new = ("активен" if old else "отключён"), ("активен" if new else "отключён")
                if attr == "role":
                    old, new = ROLE_LABELS.get(old, old), ROLE_LABELS.get(new, new)
                audit.record("Пользователи", "update", entity, attr, old, new)
    db.session.flush()
    mapping = {entry.key: entry.user.id for entry in plan.entries if entry.user is not None and entry.final}
    upload = [entry.key for entry in active if entry.final.get("_avatar")]
    return plan.as_dict(), True, mapping, upload, deactivated


def final_path(final):
    return path_text(final.get("department"))


def _dep_name(department_id):
    row = db.session.get(Department, department_id)
    return row.name if row is not None else str(department_id)


# ---------------------------------------------------------------- фото
def set_avatars(files, *, max_bytes):
    """{id сотрудника (строка поля формы): bytes} → ({id: ошибка}, число загруженных). Тип — по содержимому."""
    errors, saved = {}, 0
    ids = {}
    for name in files:
        try:
            ids[name] = int(name)
        except (TypeError, ValueError):
            errors[name] = "сотрудник не найден"
    users = {user.id: user for user in db.session.execute(select(User).where(User.id.in_(list(ids.values())))).scalars()} \
        if ids else {}
    limit = max_bytes // (1024 * 1024)
    for name, user_id in ids.items():
        raw = files[name]
        user = users.get(user_id)
        if user is None:
            errors[name] = "сотрудник не найден"
            continue
        if not raw:
            errors[name] = "файл пустой"
            continue
        if len(raw) > max_bytes:
            errors[name] = f"файл больше {limit} МБ"
            continue
        content_type = sniff_image(raw)
        if content_type is None:
            errors[name] = "допустимы PNG, JPEG, GIF и WebP"
            continue
        store_avatar(user, content_type, raw)
        audit.record("Пользователи", "update", f"user #{user.id} ({user.name})", "avatar", "", "фото из файла сотрудников")
        saved += 1
    return errors, saved
