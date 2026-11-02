"""Обновление плана из MS Project — мастер-копия ведётся в MS Project (ADR-0012, раздел 4).

`diff_plan(план, документ загрузки)` сопоставляет строки плана со строками файла по GUID задачи
(`extra.msp.guid`), затем по UID — для файлов без GUID — и даёт план изменений: новые строки, изменённые (поле
«было → стало»), отсутствующие в файле, связи (добавленные, удалённые, изменённые) и новых исполнителей.
`to_ops(изменения, параметры)` переводит план изменений в операции плана формата 0.7.0.

Что меняет обновление — только то, что пришло из файла (поля MS Project):

* название, родитель, порядок; сроки, режим и длительность; прогресс, статус, дата завершения; исполнитель;
  крайний срок и ограничение; сдвиг — только если файл загружен «по базовому плану»;
* категория и ссылка Jira — только если в «Пульсе» они не заданы;
* комментарии, пометки, цвета, текст на полосе, ссылки Jira и категории, заданные в «Пульсе», не трогаются.

Статус «Заблокирована» — уточнение «В работе», которого нет в MS Project: он остаётся, пока файл не скажет
«Завершена». Исполнитель — поле MS Project, только если в файле есть ресурсы: в файле без ресурсов
исполнителей назначают в «Пульсе», и обновление их не снимает. Связи меняются только между строками из файла:
связь со строкой, добавленной в «Пульсе», остаётся.

Строки, которых нет в файле (`missing`), удаляются только с параметром `deleteMissing`; строки, добавленные в
«Пульсе» (без `extra.msp`), не удаляются никогда — как и копии строк из файла, сделанные в «Пульсе» (у них тот
же GUID, что у строки, которую обновляет файл: их задача в файле есть). Строка, сменившая в файле тип (задача
стала суммарной), создаётся заново под новым ключом, прежняя удаляется — комментарии остаются у прежней (её
можно восстановить).

Ключи. `reserved_keys` — все ключи плана, в том числе удалённых строк, связей и исполнителей (корзина плана):
новые строки, связи, исполнители и строки, создаваемые заново при смене типа, их не получают. Строка, удалённая
в «Пульсе», но оставшаяся в файле, возвращается **новой строкой со свежим ключом** (не восстановлением: у
удалённой строки могли смениться тип и родитель, а её комментарии остаются в корзине) — в плане изменений она
помечена `deletedInPuls` (и `deletedKey`, если ключ известен) и попадает в предупреждение `deleted_in_puls`.
Удалённая строка узнаётся по документу плана (строки с `deleted: true`) или по ключу: строки, созданные
загрузкой, получают ключ файла (`i<UID>`), и он остаётся занятым в корзине. Связь, удалённая в MS Project и
снова добавленная, тоже получает свежий ключ.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict, dataclass, field

from app.services.msproject.text import norm_name

MS_FIELDS = {
    "task": ("name", "parentKey", "position", "start", "end", "endMode", "duration", "durationType", "progress",
             "status", "completedDate", "assigneeKey", "deadline", "constraintType", "constraintDate"),
    "milestone": ("name", "parentKey", "position", "date", "assigneeKey", "deadline", "constraintType",
                  "constraintDate"),
    "group": ("name", "parentKey", "position"),
}
# Заполняются из файла, только если в «Пульсе» пусто
FILL_IF_EMPTY = {"task": ("category", "jiraUrl"), "milestone": ("jiraUrl",), "group": ("jiraUrl",)}
LINK_FIELDS = ("type", "lag", "lagKind")
LINK_DEFAULTS = {"type": "FS", "lag": 0, "lagKind": "calendar"}
DIFF_WARNINGS = {
    "other_project": "Файл — другой проект MS Project: GUID проекта не совпадает с источником плана",
    "type_changed": "Строки сменили тип в MS Project — они будут созданы заново, комментарии останутся у прежних "
                    "(удалённых) строк",
    "duplicate_rows": "В плане несколько строк с одной задачей MS Project — обновляется первая, копии остаются "
                      "строками «Пульса»",
    "deleted_in_puls": "Строки, удалённые в «Пульсе», есть в файле — они будут созданы заново (удалённые остаются "
                       "в корзине плана)",
}


@dataclass
class Changes:
    """План изменений. Ключи строк, связей и исполнителей — ключи плана (у новых — выбранные свободные)."""

    new: list = field(default_factory=list)          # [{key, importKey, type, name, order, item}]
    # [{key, importKey, type, name, order, fields, extra?}]; сменившие тип — {recreate, newKey, item}
    changed: list = field(default_factory=list)
    meta: list = field(default_factory=list)         # [{key, order, extra}] — изменились только UID/GUID/WBS строки
    missing: list = field(default_factory=list)      # [{key, type, name, msp}]
    puls_only: list = field(default_factory=list)    # [{key, type, name}] — строки, добавленные в «Пульсе»
    unchanged: int = 0
    links: dict = field(default_factory=lambda: {"added": [], "removed": [], "changed": []})
    assignees: dict = field(default_factory=lambda: {"added": [], "matched": {}})
    comments: list = field(default_factory=list)     # [{itemKey, text}] — заметки новых строк
    key_map: dict = field(default_factory=dict)      # ключ документа загрузки → ключ плана
    warnings: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    def to_dict(self):
        data = asdict(self)
        data["pulsOnly"] = data.pop("puls_only")
        data["keyMap"] = data.pop("key_map")
        return data


def _msp(item):
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    msp = extra.get("msp") if isinstance(extra.get("msp"), dict) else {}
    guid = str(msp.get("guid") or "").strip().strip("{}").upper() or None
    uid = msp.get("uid")
    try:
        uid = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        uid = None
    return guid, uid, msp


def _norm(name, value):
    if value == "":
        return None
    if name in ("start", "end", "date", "completedDate", "deadline", "constraintDate") and value:
        return str(value)[:10]
    if name == "progress":
        return int(round(value or 0))
    if name in ("position",) and value is not None:
        return float(value)
    return value


def _link_value(link, name):
    value = link.get(name)
    return LINK_DEFAULTS[name] if value in (None, "") else value


def _free_key(wanted, used):
    key, n = wanted, 1
    while key in used:
        n += 1
        key = f"{wanted}x{n}"
    used.add(key)
    return key


def _increasing(values):
    """Индексы наибольшей строго возрастающей подпоследовательности среди известных позиций (None — пропуск)."""
    tails, tail_index, previous = [], [], {}
    for index, value in enumerate(values):
        if value is None:
            continue
        at = bisect_left(tails, value)
        if at == len(tails):
            tails.append(value)
            tail_index.append(index)
        else:
            tails[at] = value
            tail_index[at] = index
        previous[index] = tail_index[at - 1] if at > 0 else None
    out = []
    node = tail_index[-1] if tail_index else None
    while node is not None:
        out.append(node)
        node = previous[node]
    return set(out)


def _positions(values, base):
    """Позиции строк одного родителя в порядке файла: сохранённые — как есть, остальные — между соседями."""
    keep = _increasing(values)
    result = [values[i] if i in keep else None for i in range(len(values))]
    i, n = 0, len(result)
    while i < n:
        if result[i] is not None:
            i += 1
            continue
        j = i
        while j < n and result[j] is None:
            j += 1
        low = result[i - 1] if i > 0 else None
        high = result[j] if j < n else None
        run = j - i
        for k in range(run):
            if low is not None and high is not None:
                result[i + k] = low + (high - low) * (k + 1) / (run + 1)
            elif low is not None:
                result[i + k] = low + 1024.0 * (k + 1)
            elif high is not None:
                result[i + k] = high - 1024.0 * (run - k)
            else:
                result[i + k] = base + 1024.0 * (k + 1)
        i = j
    return result


def _settings(document):
    settings = document.get("settings")
    if not isinstance(settings, dict):
        plan = document.get("plan") if isinstance(document.get("plan"), dict) else {}
        settings = plan.get("settings") if isinstance(plan.get("settings"), dict) else {}
    return settings


def diff_plan(existing_document, import_document, *, reserved_keys=()):
    """План «Пульса» (items, links, assignees, settings) + документ загрузки (map_project) → `Changes`.

    reserved_keys — ключи, занятые в плане помимо документа (удалённые строки и связи): новые ключи их обходят."""
    changes = Changes()
    warnings = {}

    def warn(code, example=None):
        entry = warnings.setdefault(code, {"count": 0, "examples": []})
        entry["count"] += 1
        if example and len(entry["examples"]) < 5:
            entry["examples"].append(example)

    reserved_keys = set(reserved_keys or ())
    all_items = [i for i in existing_document.get("items") or [] if isinstance(i, dict) and i.get("key")]
    alive = [i for i in all_items if not i.get("deleted")]
    alive_keys = {i["key"] for i in alive}
    used_items = {i["key"] for i in all_items} | reserved_keys
    deleted_by_guid, deleted_by_uid = {}, {}
    for item in all_items:
        if item.get("deleted"):
            guid, uid, _ = _msp(item)
            if guid:
                deleted_by_guid.setdefault(guid, item["key"])
            if uid is not None:
                deleted_by_uid.setdefault(uid, item["key"])
    imported = list(import_document.get("items") or [])
    import_options = import_document.get("options") or {}

    source = _settings(existing_document).get("source") or {}
    file_guid = ((import_document.get("project") or {}).get("guid") or "").strip("{}").upper()
    plan_guid = str(source.get("projectGuid") or "").strip("{}").upper()
    if plan_guid and file_guid and plan_guid != file_guid:
        warn("other_project", (import_document.get("project") or {}).get("name"))

    # ---- сопоставление строк: GUID, затем UID (если у одной из сторон GUID нет)
    by_guid, by_uid = {}, {}
    for item in alive:
        guid, uid, _ = _msp(item)
        if guid:
            if guid in by_guid:
                warn("duplicate_rows", item.get("name"))
            by_guid.setdefault(guid, item)
        if uid is not None:
            by_uid.setdefault(uid, item)
    match, taken = {}, set()
    for item in imported:  # сначала все совпадения по GUID: UID не должен перехватить строку другой задачи
        guid = _msp(item)[0]
        candidate = by_guid.get(guid) if guid else None
        if candidate is not None and candidate["key"] not in taken:
            match[item["key"]] = candidate
            taken.add(candidate["key"])
    for item in imported:
        guid, uid, _ = _msp(item)
        if item["key"] in match or uid is None:
            continue
        other = by_uid.get(uid)
        if other is not None and other["key"] not in taken:
            other_guid = _msp(other)[0]
            if not (other_guid and guid and other_guid != guid):
                match[item["key"]] = other
                taken.add(other["key"])

    key_map, recreate = {}, set()
    for item in imported:
        existing = match.get(item["key"])
        if existing is not None and existing.get("type") == item.get("type"):
            key_map[item["key"]] = existing["key"]
        else:
            if existing is not None:
                recreate.add(item["key"])
                warn("type_changed", item.get("name"))
            key_map[item["key"]] = _free_key(item["key"], used_items)
    changes.key_map = dict(key_map)

    # ---- исполнители: по сотруднику, затем по ФИО
    all_assignees = [a for a in existing_document.get("assignees") or [] if isinstance(a, dict)]
    existing_assignees = [a for a in all_assignees if not a.get("deleted")]
    used_assignees = {a.get("key") for a in all_assignees} | reserved_keys
    by_user = {a["userId"]: a for a in existing_assignees if a.get("userId") is not None}
    by_name = {}
    for assignee in existing_assignees:
        by_name.setdefault(norm_name(assignee.get("name")), assignee)
    assignee_map = {}
    next_position = max([float(a.get("position") or 0) for a in existing_assignees] + [0.0])
    for assignee in import_document.get("assignees") or []:
        found = by_user.get(assignee.get("userId")) if assignee.get("userId") is not None else None
        found = found or by_name.get(norm_name(assignee.get("name")))
        if found is not None:
            assignee_map[assignee["key"]] = found["key"]
            changes.assignees["matched"][assignee["key"]] = found["key"]
            continue
        key = _free_key(assignee["key"], used_assignees)
        assignee_map[assignee["key"]] = key
        next_position += 1
        changes.assignees["added"].append({**assignee, "key": key, "position": next_position})
    manage_assignee = bool(import_document.get("assignees"))

    def target(item):
        out = dict(item)
        out["key"] = key_map[item["key"]]
        parent = item.get("parentKey")
        out["parentKey"] = key_map.get(parent) if parent else None
        if out.get("assigneeKey"):
            out["assigneeKey"] = assignee_map.get(out["assigneeKey"])
        return out

    targets = [target(item) for item in imported]

    # ---- порядок: позиции строк, уже стоящих в нужном порядке, сохраняются
    siblings = {}
    for index, wanted in enumerate(targets):
        siblings.setdefault(wanted["parentKey"], []).append(index)
    import_keys = {key_map[i["key"]] for i in imported}
    # Наибольшая позиция строк родителя, которых нет в файле (строки «Пульса»), — один проход по плану
    others = {}
    for item in alive:
        if item["key"] not in import_keys:
            parent = item.get("parentKey")
            others[parent] = max(others.get(parent, 0.0), float(item.get("position") or 0))
    for parent, indexes in siblings.items():
        values = []
        for index in indexes:
            existing = match.get(imported[index]["key"])
            if existing is not None and imported[index]["key"] not in recreate and existing.get("parentKey") == parent:
                values.append(float(existing.get("position") or 0))
            else:
                values.append(None)
        for index, position in zip(indexes, _positions(values, others.get(parent, 0.0)), strict=True):
            targets[index]["position"] = position

    # ---- строки
    for order, (item, wanted) in enumerate(zip(imported, targets, strict=True)):
        existing = match.get(item["key"])
        kind = item.get("type")
        if existing is None or item["key"] in recreate:
            entry = {"key": wanted["key"], "importKey": item["key"], "type": kind, "name": item.get("name"),
                     "order": order, "item": wanted}
            if existing is None:
                guid, uid, _ = _msp(item)
                deleted = (deleted_by_guid.get(guid) if guid else None) or (
                    deleted_by_uid.get(uid) if uid is not None and not guid else None)
                if deleted is None and item["key"] in reserved_keys and item["key"] not in alive_keys:
                    deleted = item["key"]   # ключ строки, созданной загрузкой, — в корзине плана
                if deleted is not None:
                    entry.update({"deletedInPuls": True, "deletedKey": deleted})
                    warn("deleted_in_puls", item.get("name"))
                changes.new.append(entry)
            else:
                entry.update({"key": existing["key"], "newKey": wanted["key"], "recreate": True,
                              "fields": {"type": {"before": existing.get("type"), "after": kind}}})
                changes.changed.append(entry)
            continue
        fields = {}
        for name in MS_FIELDS.get(kind, ()):
            if name == "assigneeKey" and not manage_assignee:
                continue
            if name in ("duration", "durationType") and wanted.get("endMode") != "duration":
                continue
            before, after = _norm(name, existing.get(name)), _norm(name, wanted.get(name))
            if name == "status" and before == "blocked" and after in ("new", "inwork"):
                continue
            if name == "position":
                if before is not None and abs(before - after) < 1e-9:
                    continue
            if before != after:
                fields[name] = {"before": before, "after": after}
        if kind == "task" and import_options.get("baseline") == "baseline":
            before, after = int(existing.get("offset") or 0), int(wanted.get("offset") or 0)
            if before != after:
                fields["offset"] = {"before": before, "after": after}
        for name in FILL_IF_EMPTY.get(kind, ()):
            if not existing.get(name) and wanted.get(name):
                fields[name] = {"before": None, "after": wanted[name]}
        entry = {"key": existing["key"], "importKey": item["key"], "type": kind, "name": wanted.get("name"),
                 "order": order, "fields": fields}
        if _msp(existing)[2] != _msp(wanted)[2]:
            entry["extra"] = {**(existing.get("extra") or {}), "msp": _msp(wanted)[2]}
        if fields:
            changes.changed.append(entry)
        else:
            changes.unchanged += 1
            if "extra" in entry:
                changes.meta.append({"key": entry["key"], "order": order, "extra": entry["extra"]})

    matched_keys = {existing["key"] for key, existing in match.items() if key not in recreate}
    replaced = {match[key]["key"] for key in recreate}
    file_guids, file_uids = set(), set()
    for item in imported:
        guid, uid, _ = _msp(item)
        file_guids.add(guid)
        file_uids.add(uid)
    for item in alive:
        if item["key"] in matched_keys or item["key"] in replaced:
            continue
        guid, uid, msp = _msp(item)
        if (guid and guid in file_guids) or (not guid and uid is not None and uid in file_uids):
            # Копия строки из файла, сделанная в «Пульсе» (тот же GUID): задача в файле есть — не «отсутствует»
            changes.puls_only.append({"key": item["key"], "type": item.get("type"), "name": item.get("name"),
                                      "copyOf": msp})
            continue
        if guid or uid is not None:
            changes.missing.append({"key": item["key"], "type": item.get("type"), "name": item.get("name"),
                                    "msp": msp})
        else:
            changes.puls_only.append({"key": item["key"], "type": item.get("type"), "name": item.get("name")})

    created = {entry["importKey"] for entry in changes.new} | recreate
    for comment in import_document.get("comments") or []:
        if comment.get("itemKey") in created:
            changes.comments.append({"itemKey": key_map[comment["itemKey"]], "text": comment.get("text")})

    # ---- связи: только между строками из файла
    all_links = [link for link in existing_document.get("links") or [] if isinstance(link, dict)]
    used_links = {link.get("key") for link in all_links} | set(reserved_keys)
    existing_pairs = {}
    for link in all_links:
        if not link.get("deleted"):
            existing_pairs.setdefault((link.get("from"), link.get("to")), link)
    wanted_pairs = set()
    for link in import_document.get("links") or []:
        source_key, target_key = key_map.get(link.get("from")), key_map.get(link.get("to"))
        if source_key is None or target_key is None:
            continue
        wanted_pairs.add((source_key, target_key))
        current = existing_pairs.get((source_key, target_key))
        values = {name: _link_value(link, name) for name in LINK_FIELDS}
        if current is None:
            changes.links["added"].append({"key": _free_key(link["key"], used_links), "from": source_key,
                                           "to": target_key, **values})
            continue
        diffs = {name: {"before": _link_value(current, name), "after": values[name]}
                 for name in LINK_FIELDS if _link_value(current, name) != values[name]}
        if diffs:
            changes.links["changed"].append({"key": current["key"], "from": source_key, "to": target_key,
                                             "fields": diffs})
    for (source_key, target_key), link in existing_pairs.items():
        if source_key in matched_keys and target_key in matched_keys and (source_key, target_key) not in wanted_pairs:
            changes.links["removed"].append({"key": link["key"], "from": source_key, "to": target_key,
                                             **{name: _link_value(link, name) for name in LINK_FIELDS}})
    changes.links["removed"].sort(key=lambda link: link["key"])

    changes.warnings = [{"code": code, "message": DIFF_WARNINGS[code], **warnings[code]}
                        for code in DIFF_WARNINGS if code in warnings]
    changes.counts = {
        "new": len(changes.new), "changed": sum(1 for c in changes.changed if not c.get("recreate")),
        "recreated": len(recreate), "unchanged": changes.unchanged, "missing": len(changes.missing),
        "pulsOnly": len(changes.puls_only), "linksAdded": len(changes.links["added"]),
        "linksRemoved": len(changes.links["removed"]), "linksChanged": len(changes.links["changed"]),
        "assigneesAdded": len(changes.assignees["added"]),
        "deletedInPuls": sum(1 for entry in changes.new if entry.get("deletedInPuls")),
    }
    return changes


def to_ops(changes, options=None):
    """План изменений → операции плана 0.7.0 в порядке применения.

    Порядок: новые исполнители; удаление строк, сменивших тип; создание строк (родители раньше вложенных);
    изменения строк (и UID/GUID/WBS в extra.msp); удаление отсутствующих в файле (только с
    options["deleteMissing"]); удаление, изменение и создание связей — после строк, удаление раньше создания
    (перевёрнутая связь не выглядит циклом)."""
    options = options or {}
    if isinstance(changes, dict):
        changes = _from_dict(changes)
    ops = [{"op": "assignee.create", "assignee": dict(a)} for a in changes.assignees["added"]]
    recreated = sorted((c for c in changes.changed if c.get("recreate")), key=lambda c: c["order"])
    ops += [{"op": "item.delete", "key": c["key"]} for c in recreated]
    creates = sorted(changes.new + recreated, key=lambda c: c["order"])
    ops += [{"op": "item.create", "item": dict(c["item"])} for c in creates]
    updates = [c for c in changes.changed if not c.get("recreate")] + list(changes.meta)
    for entry in sorted(updates, key=lambda c: c["order"]):
        fields = {name: value["after"] for name, value in (entry.get("fields") or {}).items()}
        if "extra" in entry:
            fields["extra"] = entry["extra"]
        ops.append({"op": "item.update", "key": entry["key"], "fields": fields})
    deleted = set()
    if options.get("deleteMissing"):
        for entry in changes.missing:
            ops.append({"op": "item.delete", "key": entry["key"]})
            deleted.add(entry["key"])
    deleted |= {c["key"] for c in recreated}
    for link in changes.links["removed"]:
        if link["from"] not in deleted and link["to"] not in deleted:
            ops.append({"op": "link.delete", "key": link["key"]})
    for link in sorted(changes.links["changed"], key=lambda link: link["key"]):
        ops.append({"op": "link.update", "key": link["key"],
                    "fields": {name: value["after"] for name, value in link["fields"].items()}})
    ops += [{"op": "link.create", "link": dict(link)} for link in changes.links["added"]]
    return ops


def _from_dict(data):
    return Changes(new=data.get("new") or [], changed=data.get("changed") or [], meta=data.get("meta") or [],
                   missing=data.get("missing") or [],
                   puls_only=data.get("pulsOnly") or [], unchanged=data.get("unchanged") or 0,
                   links=data.get("links") or {"added": [], "removed": [], "changed": []},
                   assignees=data.get("assignees") or {"added": [], "matched": {}},
                   comments=data.get("comments") or [], key_map=data.get("keyMap") or {},
                   warnings=data.get("warnings") or [], counts=data.get("counts") or {})
