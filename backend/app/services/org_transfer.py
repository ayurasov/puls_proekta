"""Перенос структуры компании файлом ``podelu.org-structure`` версии 1 (ADR-0008).

Формат общий с todo-planner «По Делу» (ADR-0051 там): файл, выгруженный одним инструментом,
загружается в другой. В файле — настройки структуры и дерево подразделений с типом, порядком,
внешним ключом и руководителями по email.

* «Пульс» выгружает свои настройки структуры под ключами «По Делу»: ``org_unit_kinds`` (типы
  подразделений) и ``org_max_depth`` (глубина вложенности). Остальные настройки «По Делу»
  (термины, правила поручений и видимости, вид оргсхемы) в «Пульсе» не используются: при загрузке
  они пропускаются с пометкой в плане.
* У руководителя, кроме email, выгружается логин: в «Пульсе» email у сотрудника может быть пустым.
  «По Делу» лишнее поле не читает.

Загрузка — как у импорта CSV: сначала план без записи в БД, затем то же самое одной транзакцией.
Подразделения сопоставляются по пути от корня (названия без учёта регистра и лишних пробелов),
поэтому файл подходит и для пустой системы, и для повторной загрузки. Подразделения, которых нет
в файле, не удаляются; руководители только добавляются.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import Department, DepartmentHead, User
from app.podelu_client import is_uuid
from app.services import org
from app.services.org import OrgSnapshot, norm
from app.services.org_import import clean
from app.version import APP_VERSION

FORMAT = "podelu.org-structure"
VERSION = 1
PRODUCT = "puls"
MAX_DEPARTMENTS = 5000
KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SETTING_KEYS = ("org_unit_kinds", "org_max_depth")
SETTING_LABELS = {"org_unit_kinds": "Типы подразделений", "org_max_depth": "Глубина вложенности"}
# Настройки «По Делу», которых в «Пульсе» нет
FOREIGN_SETTINGS = {
    "org_terminology": "термины структуры",
    "delegation_max_depth": "длина цепочки поручений",
    "org_heads_any_role": "руководителем может быть любой сотрудник",
    "org_default_view": "вид оргсхемы по умолчанию",
    "delegation_scope": "кому руководитель может поручать",
    "delegation_notify_line_manager": "уведомления о поручении «через голову»",
    "manager_visibility_scope": "что видит руководитель",
}


class StructureFormatError(ValueError):
    """Файл не похож на выгрузку структуры или повреждён."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- выгрузка
def export_structure():
    """Структура и её настройки — словарь для JSON."""
    snap = OrgSnapshot()
    settings = org.get_settings()
    departments = []
    for dep in snap.ordered():
        heads = []
        for user_id, primary in snap.heads.get(dep.id, []):
            user = snap.users.get(user_id)
            if user is not None:
                heads.append({"email": (user.email or "").strip(), "primary": bool(primary), "login": user.login})
        departments.append({
            "key": dep.id, "name": dep.name, "parent": dep.parent_id, "kind": dep.kind or "department",
            "sortOrder": dep.sort_order or 0, "externalId": dep.external_id,
            "heads": sorted(heads, key=lambda h: (not h["primary"], h["email"].lower(), h["login"].lower())),
        })
    return {
        "format": FORMAT,
        "version": VERSION,
        "exportedAt": _now_iso(),
        "app": APP_VERSION,
        "product": PRODUCT,
        "settings": {"org_unit_kinds": settings["unitKinds"], "org_max_depth": settings["maxDepth"]},
        "departments": departments,
    }


# ---------------------------------------------------------------- разбор и план
@dataclass
class _Department:
    key: str
    name: str
    parent: str | None
    kind: str
    sort_order: int
    external_id: str | None
    heads: list


def parse_bundle(bundle):
    """Проверка формата → (настройки «Пульса», подразделения в порядке «родители раньше детей», чужие настройки)."""
    if isinstance(bundle, str):
        try:
            bundle = json.loads(bundle)
        except ValueError as error:
            raise StructureFormatError("Файл не является JSON") from error
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise StructureFormatError("Это не файл структуры компании (format: podelu.org-structure)")
    if bundle.get("version") != VERSION:
        raise StructureFormatError(f"Версия файла {bundle.get('version')} не поддерживается — нужна {VERSION}")
    items = bundle.get("departments")
    if not isinstance(items, list):
        raise StructureFormatError("В файле нет списка подразделений")
    if len(items) > MAX_DEPARTMENTS:
        raise StructureFormatError(f"Не больше {MAX_DEPARTMENTS} подразделений в одном файле")
    departments, keys = [], set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise StructureFormatError(f"Подразделение №{index}: ожидается объект")
        key, name = str(item.get("key") or ""), clean(item.get("name"))
        if not key or not name:
            raise StructureFormatError(f"Подразделение №{index}: нужны key и name")
        if key in keys:
            raise StructureFormatError(f"Подразделение №{index}: key «{key}» повторяется")
        parent = item.get("parent")
        if parent is not None and str(parent) not in keys:
            raise StructureFormatError(f"Подразделение «{name}»: родитель «{parent}» должен идти в файле раньше")
        heads = []
        for head in item.get("heads") or []:
            if not isinstance(head, dict):
                continue
            email = str(head.get("email") or "").strip().lower()
            login = str(head.get("login") or "").strip()
            if email or login:
                heads.append({"email": email, "login": login, "primary": bool(head.get("primary"))})
        try:
            sort_order = max(-100000, min(100000, int(item.get("sortOrder") or 0)))
        except (TypeError, ValueError, OverflowError):
            sort_order = 0
        keys.add(key)
        external_id = str(item["externalId"])[:255] if item.get("externalId") else None
        departments.append(_Department(key, name[:200], str(parent) if parent is not None else None,
                                       str(item.get("kind") or "department"), sort_order, external_id, heads))
    raw = bundle.get("settings") if isinstance(bundle.get("settings"), dict) else {}
    settings = {key: raw[key] for key in SETTING_KEYS if key in raw}
    foreign = [key for key in FOREIGN_SETTINGS if key in raw]
    return settings, departments, foreign


def _validate_kinds(value):
    """Типы подразделений из файла → (список, ошибка); правила — как у PUT /api/org/settings."""
    if not isinstance(value, list) or not 1 <= len(value) <= 30:
        return None, "org_unit_kinds: список из 1–30 типов"
    seen, result = set(), []
    for item in value:
        if not isinstance(item, dict):
            return None, "org_unit_kinds: каждый тип — объект {value, label, color}"
        kind, label, color = item.get("value"), clean(item.get("label")), item.get("color") or "#64748b"
        if not isinstance(kind, str) or not KIND_RE.match(kind):
            return None, "org_unit_kinds: код типа — латиница, цифры, «-» и «_», до 32 символов"
        if kind in seen:
            return None, f"org_unit_kinds: код «{kind}» повторяется"
        if not label or len(label) > 60:
            return None, f"org_unit_kinds: название типа «{kind}» — от 1 до 60 символов"
        if not isinstance(color, str) or not COLOR_RE.match(color):
            return None, f"org_unit_kinds: цвет типа «{kind}» в формате #RRGGBB"
        seen.add(kind)
        result.append({"value": kind, "label": label, "color": color.lower()})
    return result, None


def _merged_kinds(incoming, current):
    """Типы из файла плюс типы, которые уже используются в структуре и в файле отсутствуют."""
    values = {item["value"] for item in incoming}
    used = set(db.session.execute(select(Department.kind).distinct()).scalars())
    by_value = {item["value"]: item for item in current}
    extra = [by_value.get(kind) or {"value": kind, "label": kind, "color": "#64748b"} for kind in sorted(used - values)]
    return list(incoming) + extra


def _display(key, value):
    if key == "org_unit_kinds":
        return ", ".join(f"{item.get('label')} ({item.get('value')})" for item in value or [] if isinstance(item, dict))
    return str(value)


@dataclass
class StructurePlan:
    settings: list = field(default_factory=list)      # [{key, label, current, incoming}]
    departments: list = field(default_factory=list)   # [{key, path, action, changes}]
    heads: list = field(default_factory=list)          # [{departmentKey, path, email, name, primary, action, userId}]
    missing_in_file: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    settings_patch: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)        # ключ файла → id существующего подразделения | None
    items: list = field(default_factory=list)
    podelu: bool = False                               # структура из «По Делу» (ADR-0009)
    removed: list = field(default_factory=list)        # связаны с «По Делу», но там их больше нет

    def as_dict(self):
        actions = [item["action"] for item in self.departments]
        data = {
            "summary": {
                "departments": len(self.departments),
                "create": actions.count("create"),
                "update": actions.count("update"),
                "unchanged": actions.count("unchanged"),
                "settings": len(self.settings),
                "addHeads": sum(1 for h in self.heads if h["action"] == "add"),
                "pendingHeads": sum(1 for h in self.heads if h["action"] == "pending"),
                "missingInFile": len(self.missing_in_file),
                "errors": len(self.errors),
            },
            "settings": self.settings,
            "departments": self.departments,
            "heads": self.heads,
            "missingInFile": self.missing_in_file,
            "errors": self.errors,
            "notes": self.notes,
            "canApply": not self.errors,
        }
        if self.podelu:
            data["summary"]["removedInPodelu"] = len(self.removed)
            data["removedInPodelu"] = self.removed
        return data


def plan_structure(bundle, *, apply_settings=True, podelu=False):
    """План загрузки без записи в БД. Подразделения сопоставляются по пути от корня.

    podelu=True — структура из подключённой «По Делу» (ADR-0009): подразделение, уже связанное с «По Делу»
    (podelu_id = ключ файла), сопоставляется по этой связи — переименование и перенос в «По Делу» меняют его на
    месте (тот же id, руководители, сотрудники, правила доступа). По пути сопоставляются только подразделения без
    действующей связи (связи нет или подразделения с таким UUID в «По Делу» больше нет)."""
    plan = StructurePlan(podelu=podelu)
    settings, departments, foreign = parse_bundle(bundle)
    plan.items = departments
    current = org.get_settings()
    kinds, max_depth = current["unitKinds"], current["maxDepth"]
    if foreign:
        plan.notes.append("Настройки «По Делу», которых нет в «Пульсе», не переносятся: "
                          + ", ".join(FOREIGN_SETTINGS[key] for key in foreign))
    if apply_settings and "org_unit_kinds" in settings:
        incoming, error = _validate_kinds(settings["org_unit_kinds"])
        if error:
            plan.errors.append(error)
        else:
            merged = _merged_kinds(incoming, kinds)
            if _display("org_unit_kinds", kinds) != _display("org_unit_kinds", merged) \
                    or [k["color"] for k in kinds] != [k["color"] for k in merged]:
                plan.settings.append({"key": "org_unit_kinds", "label": SETTING_LABELS["org_unit_kinds"],
                                      "current": _display("org_unit_kinds", kinds),
                                      "incoming": _display("org_unit_kinds", merged)})
                plan.settings_patch["unitKinds"] = merged
            kinds = merged
    if apply_settings and "org_max_depth" in settings:
        try:
            depth = max(2, min(30, int(settings["org_max_depth"])))
        except (TypeError, ValueError, OverflowError):
            plan.errors.append("org_max_depth: ожидается целое число")
        else:
            if depth != max_depth:
                plan.settings.append({"key": "org_max_depth", "label": SETTING_LABELS["org_max_depth"],
                                      "current": str(max_depth), "incoming": str(depth)})
                plan.settings_patch["maxDepth"] = depth
            max_depth = depth
    kind_values = {item["value"] for item in kinds}

    # Подразделения: связь с «По Делу» (podelu=True), затем путь от корня → существующее или новое
    snap = OrgSnapshot()
    children = {(dep.parent_id, norm(dep.name)): dep for dep in snap.departments.values()}
    keys = {item.key for item in departments}
    linked = {}
    if podelu:
        for dep in sorted(snap.departments.values(), key=lambda d: d.id):
            if dep.podelu_id and dep.podelu_id in keys:
                linked.setdefault(dep.podelu_id, dep)
    claimed = {dep.id for dep in linked.values()}
    paths, depth_of, matched_ids, siblings = {}, {}, set(), {}
    for item in departments:
        parent_target = plan.targets.get(item.parent) if item.parent else None
        path = [*paths.get(item.parent, []), item.name]
        paths[item.key] = path
        depth_of[item.key] = depth_of.get(item.parent, 0) + 1
        label = " / ".join(path)
        if item.kind not in kind_values:
            plan.errors.append(f"{label}: тип «{item.kind}» не задан в настройках")
        sibling = (item.parent, norm(item.name))
        if sibling in siblings:
            plan.errors.append(f"{label}: на этом уровне файла уже есть подразделение с таким названием")
        siblings[sibling] = item.key
        row = linked.get(item.key)
        if row is None:
            row = children.get((parent_target, norm(item.name))) if (item.parent is None or parent_target) else None
            if row is not None and (row.id in claimed or row.id in matched_ids):
                row = None   # уже сопоставлено с другим подразделением «По Делу»
        if row is None:
            plan.targets[item.key] = None
            plan.departments.append({"key": item.key, "path": label, "action": "create", "changes": []})
            continue
        plan.targets[item.key] = row.id
        matched_ids.add(row.id)
        changes = []
        # «По Делу» ведёт название: и у связанного, и у совпавшего по пути без учёта регистра и пробелов
        if podelu and row.name != item.name:
            changes.append({"field": "name", "old": row.name, "new": item.name})
        if row.id in claimed:
            # Перенос в «По Делу» — то же подразделение «Пульса»
            # parent_target None при родителе из файла — родитель новый: перенос в него
            moved = row.parent_id is not None if item.parent is None \
                else parent_target is None or parent_target != row.parent_id
            if moved:
                changes.append({"field": "parent", "old": " / ".join(snap.path(row.parent_id)) or "верхний уровень",
                                "new": " / ".join(path[:-1]) or "верхний уровень"})
        if (row.kind or "department") != item.kind:
            changes.append({"field": "kind", "old": row.kind, "new": item.kind})
        if (row.sort_order or 0) != item.sort_order:
            changes.append({"field": "sortOrder", "old": row.sort_order or 0, "new": item.sort_order})
        plan.departments.append({"key": item.key, "path": label, "action": "update" if changes else "unchanged",
                                 "changes": changes})
    if podelu:
        _check_final_structure(plan, departments, snap, paths, max_depth)
        unmatched = [snap.departments[dep_id] for dep_id in snap.departments if dep_id not in matched_ids]
        plan.removed = sorted((" / ".join(snap.path(dep.id)) for dep in unmatched
                               if dep.podelu_id and dep.podelu_id not in keys), key=str.casefold)
        plan.missing_in_file = sorted((" / ".join(snap.path(dep.id)) for dep in unmatched
                                       if not dep.podelu_id or dep.podelu_id in keys), key=str.casefold)
    else:
        deepest = max(depth_of.values(), default=0)
        existing_depth = max((snap.depth(dep_id) for dep_id in snap.departments), default=0)
        if max(deepest, existing_depth) > max_depth:
            plan.errors.append(f"Вложенность после загрузки — {max(deepest, existing_depth)} уровней, а допустимо "
                               f"{max_depth}: увеличьте «Глубину вложенности»")
        plan.missing_in_file = sorted((" / ".join(snap.path(dep_id)) for dep_id in snap.departments
                                       if dep_id not in matched_ids), key=str.casefold)

    # Руководители: по email (если его нет — по логину); кого нет в системе — назначит загрузка сотрудников
    by_email, by_login = {}, {}
    for user in sorted(snap.users.values(), key=lambda u: u.id):
        if user.email:
            by_email.setdefault(user.email.strip().lower(), user)
        by_login.setdefault(user.login.lower(), user)
    current_heads = {(dep_id, user_id) for dep_id, heads in snap.heads.items() for user_id, _primary in heads}
    planned = set()
    for item in departments:
        target = plan.targets.get(item.key)
        for head in item.heads:
            user = by_email.get(head["email"]) if head["email"] else by_login.get(head["login"].lower())
            if user is None:
                action = "pending"
            elif (target and (target, user.id) in current_heads) or (item.key, user.id) in planned:
                action = "exists"
            else:
                action = "add"
                planned.add((item.key, user.id))
            plan.heads.append({"departmentKey": item.key, "path": " / ".join(paths[item.key]),
                               "email": head["email"] or head["login"], "name": user.name if user else "",
                               "primary": head["primary"], "action": action, "userId": user.id if user else None})
    return plan


def _check_final_structure(plan, departments, snap, paths, max_depth):
    """Структура после переименований и переносов из «По Делу»: названия соседних не повторяются, глубина — в
    пределах настройки. Циклов не бывает: родитель подразделения из файла — тоже подразделение из файла, а файл —
    дерево (и всё равно проверяется перед сохранением, org.assert_consistent)."""
    parent_of = {dep_id: dep.parent_id for dep_id, dep in snap.departments.items()}
    name_of = {dep_id: dep.name for dep_id, dep in snap.departments.items()}
    labels = {}
    for item in departments:
        ref = plan.targets.get(item.key) or f"new:{item.key}"
        parent_of[ref] = (plan.targets.get(item.parent) or f"new:{item.parent}") if item.parent else None
        name_of[ref] = item.name
        labels[ref] = " / ".join(paths[item.key])
    groups = {}
    for ref, parent in parent_of.items():
        groups.setdefault((parent, norm(name_of[ref])), []).append(ref)
    for refs in groups.values():
        others = [ref for ref in refs if ref not in labels]
        if len(refs) > 1 and others:
            for ref in refs:
                if ref in labels:
                    plan.errors.append(f"{labels[ref]}: на этом уровне в «Пульсе» уже есть подразделение "
                                       f"«{name_of[others[0]]}», которого нет в «По Делу», — переименуйте или "
                                       "перенесите его")

    def depth(ref):
        level, seen = 0, set()
        while ref is not None and ref not in seen:
            seen.add(ref)
            level += 1
            ref = parent_of.get(ref)
        return level

    deepest = max((depth(ref) for ref in parent_of), default=0)
    if deepest > max_depth:
        plan.errors.append(f"Вложенность после загрузки — {deepest} уровней, а допустимо {max_depth}: увеличьте "
                           "«Глубину вложенности»")


def apply_structure(bundle, *, apply_settings=True, podelu=False):
    """(план, применён ли). При ошибках ничего не меняется.

    podelu=True — структура получена из подключённой «По Делу» (ADR-0009): ключ подразделения в файле — его UUID
    в «По Делу». Он записывается в podelu_id сопоставленного подразделения, а новое подразделение получает его же
    как id, если такой id в «Пульсе» свободен. Связанное подразделение переименовывается и переносится на месте."""
    plan = plan_structure(bundle, apply_settings=apply_settings, podelu=podelu)
    if plan.errors:
        return plan.as_dict(), False
    changes = {entry["key"]: {c["field"]: c for c in entry["changes"]} for entry in plan.departments}
    ids, used = {}, set()
    for item in plan.items:
        target = plan.targets.get(item.key)
        if target is None:
            dep_id = org.new_id()
            if podelu and is_uuid(item.key) and item.key not in used and db.session.get(Department, item.key) is None:
                dep_id = item.key
            used.add(dep_id)
            row = Department(id=dep_id, name=item.name, parent_id=ids.get(item.parent) if item.parent else None,
                             kind=item.kind, sort_order=item.sort_order, org_source="podelu" if podelu else "transfer",
                             external_id=item.external_id)
            db.session.add(row)
            if podelu:
                db.session.flush()   # подразделение, перенесённое в новое, ссылается на него в том же сохранении
            ids[item.key] = row.id
            audit.record("Структура", "create", item.name, "", "",
                         "загрузка структуры из «По Делу»" if podelu else "загрузка структуры из файла")
            continue
        ids[item.key] = target
        used.add(target)
        row = db.session.get(Department, target)
        moved = changes.get(item.key, {})
        if "name" in moved:
            audit.record("Структура", "update", row.name, "name", row.name, item.name)
            row.name = item.name
        if "parent" in moved:
            audit.record("Структура", "update", item.name, "parent", moved["parent"]["old"], moved["parent"]["new"])
            row.parent_id = ids.get(item.parent) if item.parent else None
        if (row.kind or "department") != item.kind:
            audit.record("Структура", "update", row.name, "kind", row.kind, item.kind)
            row.kind = item.kind
        if (row.sort_order or 0) != item.sort_order:
            row.sort_order = item.sort_order
        if item.external_id and not row.external_id:
            row.external_id = item.external_id
    db.session.flush()
    if podelu:
        link_departments({ids[item.key]: item.key for item in plan.items if is_uuid(item.key)})
    primaries = set(db.session.execute(
        select(DepartmentHead.department_id).where(DepartmentHead.is_primary.is_(True))).scalars())
    names = {user.id: user.name for user in db.session.execute(select(User)).scalars()}
    for head in plan.heads:
        if head["action"] != "add":
            continue
        department_id = ids[head["departmentKey"]]
        primary = head["primary"] and department_id not in primaries
        db.session.add(DepartmentHead(department_id=department_id, user_id=head["userId"], is_primary=primary))
        if primary:
            primaries.add(department_id)
        audit.record("Структура", "update", head["path"], "heads", "", names.get(head["userId"], ""))
    if apply_settings and plan.settings_patch:
        settings = {**org.get_settings(), **plan.settings_patch}
        org.save_settings({"unitKinds": settings["unitKinds"], "maxDepth": settings["maxDepth"]})
        audit.record("Структура", "update", "Настройки структуры", "", "",
                     f"из файла: типов {len(settings['unitKinds'])}, уровней {settings['maxDepth']}")
    db.session.flush()
    return plan.as_dict(), True


def link_departments(links):
    """{id подразделения «Пульса»: UUID в «По Делу»} → podelu_id. Ссылка, которая была у другого подразделения
    (например, переименованного в «Пульсе»), переходит к сопоставленному сейчас. Возвращает число новых ссылок."""
    wanted = set(links.values())
    for row in db.session.execute(select(Department).where(Department.podelu_id.is_not(None))).scalars():
        if row.podelu_id in wanted and links.get(row.id) != row.podelu_id:
            row.podelu_id = None
    db.session.flush()
    changed = 0
    for dep_id, key in links.items():
        row = db.session.get(Department, dep_id)
        if row is not None and row.podelu_id != key:
            row.podelu_id = key
            changed += 1
    db.session.flush()
    return changed
