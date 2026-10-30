"""Формат данных диаграммы Ганта 1.0.x и преобразование в модель 0.2 и обратно.

В 1.0.x план (вкладка) хранился одним JSON-документом «state»:

    {project: {id, title, subtitle}, range, today, settings, scale, zoom, rowHeight,
     leftWidth, sidebarHidden, ganttLeftWidth, assignees: [...], rows: [...],
     notes: [...], history: [], collapsed: {rowId: true}, nextId}

Строки (`rows`) — задачи, вехи и группы; у задачи комментарии и история изменений
лежали внутри строки. Порядок вывода: корни в порядке массива, дети — сначала группы,
потом задачи, потом вехи, внутри типа — по порядку массива; родитель строки —
`parentId`, а если его нет — `groupId`.

Модуль используется:

* переносом базы 1.0.x (`scripts/import_legacy.py`);
* импортом файла JSON, сохранённого кнопкой «Экспорт JSON» в 1.0.x или 0.2;
* экспортом плана в JSON, который открывается и в 1.0.x («Импорт»).
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.services.plan_schema import DEFAULT_CATEGORIES, PALETTE, default_settings, is_color, normalize_settings

LEGACY_TZ = ZoneInfo("Europe/Moscow")

TASK_KEYS = {
    "id", "type", "name", "start", "end", "endMode", "duration", "durationType", "progress", "offset", "barText",
    "category", "color", "parentId", "groupId", "assigneeId", "dependencyId", "dependencyType", "depLag", "status",
    "comments", "changeHistory", "jiraUrl", "completedDate",
}
MILESTONE_KEYS = {"id", "type", "name", "date", "color", "parentId", "groupId", "assigneeId", "comment", "lineStyle",
                  "lineColor"}
GROUP_KEYS = {"id", "type", "name", "color", "groupId", "parentId"}
TYPE_ORDER = {"group": 0, "task": 1, "milestone": 2}


class ImportReport:
    """Замечания преобразования: что было исправлено и почему."""

    def __init__(self):
        self.notes = []

    def add(self, text):
        self.notes.append(text)


def parse_legacy_datetime(value):
    """'05.08.2026, 15:49:51' (toLocaleString ru-RU, московское время) -> наивный UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", " ")
    for fmt in ("%d.%m.%Y, %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y, %H:%M", "%d.%m.%Y"):
        try:
            local = datetime.strptime(text, fmt).replace(tzinfo=LEGACY_TZ)
            return local.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def format_legacy_datetime(value):
    if value is None:
        return ""
    local = value.replace(tzinfo=timezone.utc).astimezone(LEGACY_TZ)
    return local.strftime("%d.%m.%Y, %H:%M:%S")


def _iso(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def effective_parent(row):
    return row.get("parentId") or row.get("groupId") or None


def legacy_display_order(rows, report=None):
    """Порядок строк, как их показывала 1.0.x (без фильтров и сворачивания).

    Строки, недостижимые от корней (родитель удалён, цикл), в 1.0.x не показывались;
    при переносе они становятся корневыми и попадают в отчёт.
    """
    index = {r["id"]: i for i, r in enumerate(rows)}
    by_id = {r["id"]: r for r in rows}
    children = {}
    for row in rows:
        parent = row.get("parentId")
        if parent:
            children.setdefault(parent, []).append(row)
        elif row.get("groupId"):
            children.setdefault(row["groupId"], []).append(row)
    roots = [r for r in rows if not r.get("parentId") and not r.get("groupId")]
    out = []
    seen = set()

    def walk(row, parent_id):
        if row["id"] in seen:
            return
        seen.add(row["id"])
        out.append((row["id"], parent_id))
        kids = [c for c in children.get(row["id"], []) if c["id"] != row["id"]]
        kids.sort(key=lambda c: (TYPE_ORDER.get(c.get("type"), 3), index[c["id"]]))
        for kid in kids:
            walk(kid, row["id"])

    for root in roots:
        walk(root, None)
    for row in rows:
        if row["id"] not in seen:
            if report is not None:
                parent = effective_parent(row)
                reason = "родитель отсутствует" if parent not in by_id else "цикл во вложенности"
                report.add(f"Строка «{row.get('name')}» ({row['id']}) не отображалась в 1.0.x ({reason}) — "
                           "перенесена на верхний уровень")
            walk(row, None)
    return out


def _color(value, default, report, what):
    if is_color(value):
        return value
    if value not in (None, ""):
        report.add(f"{what}: неизвестный цвет «{value}» заменён на «{default}»")
    return default


def legacy_settings(state):
    settings = default_settings()
    rng = state.get("range") or {}
    start, end = _iso(rng.get("start")), _iso(rng.get("end"))
    if start and end and end >= start:
        settings["range"] = {"start": start, "end": end}
    today = state.get("today") or {}
    settings["today"] = {
        "auto": today.get("auto") is not False,
        "date": _iso(today.get("date")) if today.get("auto") is False else None,
        "style": today.get("style") if today.get("style") in ("dashed", "solid", "dotted") else "dashed",
        "color": today.get("color") if today.get("color") in PALETTE else "red",
    }
    if state.get("scale") in ("day", "week", "month", "quarter"):
        settings["scale"] = state["scale"]
    if isinstance(state.get("zoom"), (int, float)):
        settings["zoom"] = float(state["zoom"])
    if isinstance(state.get("rowHeight"), (int, float)):
        settings["rowHeight"] = int(state["rowHeight"])
    if isinstance(state.get("ganttLeftWidth"), (int, float)):
        settings["gridWidth"] = int(state["ganttLeftWidth"])
    src = state.get("settings") or {}
    display = settings["display"]
    for key in ("useRuCalendar", "showWeekends", "showHolidays", "showProgress", "showNotes", "autoShift",
                "legendEnabled", "showTaskNamesOnBars"):
        if isinstance(src.get(key), bool):
            display[key] = src[key]
    display["showOffsetLabels"] = src.get("showOffsetLabels") is not False
    if src.get("legendPosition") in ("top", "bottom"):
        display["legendPosition"] = src["legendPosition"]
    if src.get("legendMode") in ("all", "main"):
        display["legendMode"] = src["legendMode"]
    if isinstance(src.get("tooltipFields"), list):
        display["tooltipFields"] = list(src["tooltipFields"])
    # Связи-стрелки появились в 0.2: перенесённые планы выглядят как раньше, включить можно в настройках
    display["showDependencies"] = False
    categories = list(DEFAULT_CATEGORIES)
    for row in state.get("rows") or []:
        cat = (row.get("category") or "").strip()
        if cat and cat not in categories:
            categories.append(cat)
    settings["categories"] = categories
    settings["defaultCollapsed"] = [k for k, v in (state.get("collapsed") or {}).items() if v]
    settings["legacy"] = {k: state[k] for k in ("leftWidth", "sidebarHidden") if k in state}
    return normalize_settings(settings)


def legacy_state_to_document(state, title=None, report=None):
    """state 1.0.x -> (документ 0.2, комментарии, события истории, отчёт)."""
    report = report or ImportReport()
    rows = [r for r in (state.get("rows") or []) if isinstance(r, dict) and r.get("id")]
    seen_ids = set()
    unique_rows = []
    for row in rows:
        if row["id"] in seen_ids:
            report.add(f"Повторяющийся идентификатор строки {row['id']} — повтор пропущен")
            continue
        seen_ids.add(row["id"])
        unique_rows.append(row)
    rows = unique_rows
    by_id = {r["id"]: r for r in rows}
    order = legacy_display_order(rows, report)
    assignee_keys = set()
    assignees = []
    for i, a in enumerate(state.get("assignees") or []):
        if not isinstance(a, dict) or not a.get("id") or a["id"] in assignee_keys:
            continue
        assignee_keys.add(a["id"])
        assignees.append({
            "key": a["id"], "name": (a.get("name") or "Исполнитель").strip() or "Исполнитель",
            "role": (a.get("role") or "").strip(),
            "color": a.get("color") if is_color(a.get("color")) else "#2563eb",
            "position": float(i + 1),
        })

    items = []
    comments = []
    events = []
    for position, (row_id, parent_id) in enumerate(order, start=1):
        row = by_id[row_id]
        rtype = row.get("type") if row.get("type") in ("task", "milestone", "group") else "task"
        name = str(row.get("name") or "").strip() or "(без названия)"
        item = {"key": row_id, "type": rtype, "name": name, "parentKey": parent_id, "position": float(position * 1024)}
        known = {"task": TASK_KEYS, "milestone": MILESTONE_KEYS, "group": GROUP_KEYS}[rtype]
        extra = {k: v for k, v in row.items() if k not in known}
        # Явная группа подзадачи/вехи (в 1.0.x у строки могли быть и parentId, и groupId)
        if row.get("parentId") and row.get("groupId"):
            extra["legacyGroupId"] = row["groupId"]
        if rtype == "task":
            start = _iso(row.get("start"))
            end = _iso(row.get("end"))
            if not start and end:
                start = end
            if not end and start:
                end = start
            if not start:
                start = end = date.today().isoformat()
                report.add(f"Задача «{name}»: не было дат — поставлена сегодняшняя дата")
            if end < start:
                report.add(f"Задача «{name}»: окончание раньше начала — даты переставлены")
                start, end = end, start
            dep = row.get("dependencyId") or None
            if dep and (dep not in by_id or dep == row_id or by_id[dep].get("type") == "group"):
                report.add(f"Задача «{name}»: связь с отсутствующей строкой {dep} снята")
                dep = None
            assignee = row.get("assigneeId") or None
            if assignee and assignee not in assignee_keys:
                report.add(f"Задача «{name}»: исполнитель {assignee} не найден в справочнике — снят")
                assignee = None
            item.update({
                "start": start, "end": end,
                "endMode": row.get("endMode") if row.get("endMode") in ("date", "duration") else "date",
                "duration": _int(row.get("duration"), None) if row.get("duration") not in (None, "") else None,
                "durationType": row.get("durationType") if row.get("durationType") in ("calendar", "working") else "calendar",
                "progress": max(0, min(100, _int(row.get("progress")))),
                "offset": _int(row.get("offset")),
                "completedDate": _iso(row.get("completedDate")),
                "dependencyKey": dep,
                "dependencyType": row.get("dependencyType") if row.get("dependencyType") in ("FS", "SS", "FF", "SF") else "FS",
                "dependencyLag": _int(row.get("depLag")),
                "assigneeKey": assignee,
                "status": row.get("status") if row.get("status") in ("new", "inwork", "blocked", "done") else "new",
                "category": (row.get("category") or "").strip() or None,
                "color": _color(row.get("color"), "blue", report, f"Задача «{name}»"),
                "barText": (row.get("barText") or "").strip() or None,
                "jiraUrl": None,
            })
            url = (row.get("jiraUrl") or "").strip()
            if url:
                if url.startswith(("http://", "https://")):
                    item["jiraUrl"] = url
                else:
                    extra["legacyJiraUrl"] = url
                    report.add(f"Задача «{name}»: ссылка Jira «{url}» не http(s) — сохранена в архивных полях")
            for c in row.get("comments") or []:
                if not isinstance(c, dict):
                    continue
                comments.append({
                    "itemKey": row_id,
                    "authorName": (c.get("author") or "").strip(),
                    "text": str(c.get("text") or ""),
                    "createdAt": parse_legacy_datetime(c.get("date")),
                    "replyToIndex": c.get("replyTo") if isinstance(c.get("replyTo"), int) else None,
                    "extra": {"legacyDate": c.get("date")},
                })
            for h in row.get("changeHistory") or []:
                if not isinstance(h, dict):
                    continue
                events.append({
                    "itemKey": row_id,
                    "userName": (h.get("user") or "").strip(),
                    "createdAt": parse_legacy_datetime(h.get("date")),
                    "action": h.get("action") or "",
                    "details": h.get("details") or "",
                    "raw": h,
                })
        elif rtype == "milestone":
            day = _iso(row.get("date"))
            if not day:
                day = date.today().isoformat()
                report.add(f"Веха «{name}»: не было даты — поставлена сегодняшняя")
            assignee = row.get("assigneeId") or None
            if assignee and assignee not in assignee_keys:
                assignee = None
            color = _color(row.get("color"), "teal", report, f"Веха «{name}»")
            item.update({
                "date": day, "assigneeKey": assignee, "comment": row.get("comment") or None,
                "lineStyle": row.get("lineStyle") if row.get("lineStyle") in ("solid", "dashed", "dotted") else "dashed",
                "color": color, "lineColor": _color(row.get("lineColor") or row.get("color"), color, report, f"Веха «{name}»"),
            })
        else:
            item["color"] = _color(row.get("color"), "slate", report, f"Группа «{name}»")
        if extra:
            item["extra"] = extra
        items.append(item)

    notes = []
    note_keys = set()
    for n in state.get("notes") or []:
        if not isinstance(n, dict) or not n.get("id") or n["id"] in note_keys:
            continue
        day = _iso(n.get("date"))
        text = str(n.get("text") or "").strip()
        if not day or not text:
            report.add(f"Пометка {n.get('id')}: нет даты или текста — пропущена")
            continue
        note_keys.add(n["id"])
        row_key = n.get("rowId") or None
        if row_key and row_key not in by_id:
            report.add(f"Пометка «{text[:40]}»: строка {row_key} отсутствует — пометка отнесена ко всему графику")
            row_key = None
        notes.append({"key": n["id"], "date": day, "text": text, "rowKey": row_key,
                      "color": n.get("color") if is_color(n.get("color")) else "orange",
                      "showDate": bool(n.get("showDate"))})

    project = state.get("project") or {}
    doc = {
        "format": "puls-plan",
        "version": 1,
        "plan": {
            "key": project.get("id"),
            "title": (title or project.get("title") or "План").strip() or "План",
            "subtitle": (project.get("subtitle") or "").strip(),
            "settings": legacy_settings(state),
            "nextRowSeq": _int(state.get("nextId"), 1) or 1,
        },
        "assignees": assignees,
        "items": items,
        "notes": notes,
    }
    return doc, comments, events, report


def document_from_any(payload):
    """Документ для импорта из JSON: снимок 0.2, state 1.0.x или блоб проекта 1.0.x с вкладками."""
    import json

    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except ValueError:
            raise ValueError("Файл не является JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("Неизвестный формат файла")
    if payload.get("format") == "puls-plan":
        if not isinstance(payload.get("items"), list):
            raise ValueError("В файле нет строк плана")
        return payload, []
    if isinstance(payload.get("__tabs"), dict):
        tabs = payload["__tabs"]
        active = tabs.get("activeTabId")
        raw = (tabs.get("tabsData") or {}).get(active)
        if raw:
            payload = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(payload.get("rows"), list):
        doc, comments, _events, _report = legacy_state_to_document(payload)
        return doc, comments
    raise ValueError("Файл не похож на план «Пульса Проекта» (нет строк)")


# ---------------- экспорт в формат 1.0.x ----------------

def _legacy_action(event):
    return {"create": "Создание", "update": "Изменение", "delete": "Удаление", "restore": "Восстановление",
            "comment": "Комментарий"}.get(event.action, "Изменение")


def plan_to_legacy_state(plan, items, notes, assignees, comments_by_item=None, events_by_item=None):
    """План 0.2 -> state 1.0.x (с дополнительными полями, которые 1.0.x просто игнорирует)."""
    comments_by_item = comments_by_item or {}
    events_by_item = events_by_item or {}
    settings = normalize_settings(plan.settings)
    alive = [i for i in items if i.deleted_at is None]
    by_key = {i.key: i for i in alive}
    children = {}
    for item in alive:
        children.setdefault(item.parent_key if item.parent_key in by_key else None, []).append(item)
    for group in children.values():
        group.sort(key=lambda i: (i.position, i.id or 0))
    ordered = []

    def walk(parent_key):
        for child in children.get(parent_key, []):
            ordered.append(child)
            walk(child.key)

    walk(None)
    rows = []
    for index, item in enumerate(ordered):
        extra = dict(item.extra or {})
        legacy_group = extra.pop("legacyGroupId", None)
        extra.pop("legacyJiraUrl", None)
        parent = by_key.get(item.parent_key)
        parent_id, group_id = "", ""
        if parent is not None:
            if parent.type == "group":
                group_id = parent.key
            else:
                parent_id = parent.key
                if legacy_group and legacy_group in by_key:
                    group_id = legacy_group
        row = {"id": item.key, "type": item.type, "name": item.name}
        if item.type == "task":
            history = []
            for ev in events_by_item.get(item.key, []):
                if ev.action == "legacy" and isinstance(ev.changes, dict) and isinstance(ev.changes.get("legacy"), dict):
                    history.append(ev.changes["legacy"])
                else:
                    history.append({"date": format_legacy_datetime(ev.created_at), "user": ev.user_name,
                                    "action": _legacy_action(ev), "details": ev.summary})
            comment_rows = comments_by_item.get(item.key, [])
            ids = [c.id for c in comment_rows]
            row.update({
                "start": item.start_date.isoformat(), "end": item.end_date.isoformat(),
                "endMode": item.end_mode or "date",
                "duration": item.duration if item.duration is not None else (item.end_date - item.start_date).days + 1,
                "durationType": item.duration_type or "calendar", "progress": item.progress or 0,
                "offset": item.offset_days or 0, "barText": item.bar_text or "", "category": item.category or "",
                "color": item.color or "blue", "parentId": parent_id, "groupId": group_id,
                "assigneeId": item.assignee_key or "", "dependencyId": item.dependency_key or "",
                "dependencyType": item.dependency_type or "FS", "depLag": item.dependency_lag or 0,
                "status": item.status or "new",
                "comments": [
                    {k: v for k, v in {
                        "text": c.text, "author": c.author_name,
                        "date": (c.extra or {}).get("legacyDate") or format_legacy_datetime(c.created_at),
                        "replyTo": ids.index(c.reply_to_id) if c.reply_to_id in ids else None,
                    }.items() if v is not None}
                    for c in comment_rows
                ],
                "changeHistory": history,
            })
            if item.jira_url:
                row["jiraUrl"] = item.jira_url
            if item.completed_date:
                row["completedDate"] = item.completed_date.isoformat()
        elif item.type == "milestone":
            row.update({
                "date": item.start_date.isoformat(), "color": item.color or "teal", "parentId": parent_id,
                "assigneeId": item.assignee_key or "", "comment": item.comment or "",
                "lineStyle": item.line_style or "dashed", "lineColor": item.line_color or item.color or "teal",
            })
            if group_id:
                row["groupId"] = group_id
        else:
            row["color"] = item.color or "slate"
            if parent is not None:
                row["groupId"] = parent.key
        row.update({k: v for k, v in extra.items() if k not in row})
        row["sortIndex"] = index
        rows.append(row)

    display = settings["display"]
    today = settings["today"]
    legacy = settings.get("legacy") or {}
    return {
        "project": {"id": plan.key, "title": plan.title, "subtitle": plan.subtitle or ""},
        "range": settings["range"],
        "today": {"date": today.get("date") or date.today().isoformat(), "style": today["style"],
                  "color": today["color"], "auto": today["auto"]},
        "settings": {
            **{k: display[k] for k in ("useRuCalendar", "showWeekends", "showHolidays", "showProgress", "showNotes",
                                       "autoShift", "legendEnabled", "legendPosition", "legendMode",
                                       "showTaskNamesOnBars", "tooltipFields", "showOffsetLabels")},
        },
        "scale": settings["scale"],
        "zoom": settings["zoom"],
        "rowHeight": settings["rowHeight"],
        "leftWidth": legacy.get("leftWidth", 450),
        "sidebarHidden": legacy.get("sidebarHidden", False),
        "ganttLeftWidth": settings["gridWidth"],
        "assignees": [{"id": a.key, "name": a.name, "role": a.role or "", "color": a.color}
                      for a in sorted((a for a in assignees if a.deleted_at is None), key=lambda a: (a.position, a.id or 0))],
        "rows": rows,
        "notes": [{"id": n.key, "date": n.date.isoformat(), "text": n.text, "rowId": n.row_key or "",
                   "color": n.color or "orange", "showDate": bool(n.show_date)}
                  for n in notes if n.deleted_at is None],
        "history": [],
        "collapsed": {k: True for k in settings.get("defaultCollapsed", [])},
        "nextId": plan.next_row_seq,
    }
