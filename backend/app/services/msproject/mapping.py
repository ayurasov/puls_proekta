"""Сопоставление модели MS Project с планом «Пульса» (ADR-0012, раздел 3).

Чистая функция `map_project(model, options) → документ загрузки`: словари и списки, которые сразу уходят в JSON
предпросмотра и из которых сервер создаёт план. База, Flask и HTTP здесь не нужны — правила проверяются тестами
на файлах-образцах.

Главное:

* задача UID 0 (суммарная задача проекта) — не строка; суммарные задачи — группы, вехи (Milestone = 1 или
  длительность 0) — вехи с датой начала, остальное — задачи;
* даты — дни без часов: окончание в начале рабочего дня (08:00 и раньше) — предыдущий день;
* неактивные задачи, пустые строки и внешние задачи пропускаются, связи с ними — тоже; связи суммарных задач
  переносятся на вложенные задачи; лаг — в рабочие или календарные дни, затем подбирается так, чтобы расчёт
  сроков «Пульса» дал ровно даты файла (schedule.py: «даты в файле — главные»);
* ресурсы-работники — исполнители плана (связь с сотрудником по email, затем по ФИО), из нескольких назначений
  строки — исполнитель с наибольшими трудозатратами, остальные — в заметке;
* всё, что не перенесено или перенесено иначе, — в предупреждениях: код, текст, число и до пяти примеров;
  неожиданные значения файла (даты, длительности) — `MsProjectError`, а не ошибка сервера.

Ключи строк, связей и исполнителей выводятся из UID файла (`i12`, `l12-15`, `a3`): повторная проверка того же
файла даёт те же ключи. Если ключ уже занят в плане, сервер переназначает его при применении.
"""

from __future__ import annotations

import math
import re
from datetime import date, time, timedelta

from app.services.msproject.model import (
    ELAPSED_FORMATS,
    FINISH_CONSTRAINTS,
    FORMAT_DAYS,
    PERCENT_FORMATS,
    RESOURCE_WORK,
    DAY_TYPE_TO_ISO,
    MsProjectError,
    base_format,
    is_task_field,
)
from app.services.msproject.schedule import MAX_LAG, RuCalendar, align_links, clamp_snet
from app.services.msproject.text import clean, norm_email, norm_name, plain_text

MS_NOTE_PREFIX = "Заметка из MS Project: "
ITEM_LIMIT = 5000
DEFAULT_DAY_START = time(8, 0)
TEXT_LIMIT = 5000
RANGE_LIMIT = 366 * 15          # шкала плана — не длиннее 15 лет (plan_schema.normalize_settings)
MAX_DURATION = 36600            # длительность в днях больше 100 лет — не длительность, а ошибка файла
EXCEPTION_BUDGET = 50_000       # дат исключений календаря на файл — дальше не разворачиваются
NO_NAME = "Без названия"
# Цвета категорий — ключи палитры «Пульса» (plan_schema.PALETTE) без серого групп и бирюзового вех
CATEGORY_COLORS = ("blue", "green", "orange", "purple", "red", "amber", "pink", "indigo", "cyan", "lime")
# Цвета исполнителей — те же цвета палитры в #rrggbb (src/gantt/domain/colors.js)
ASSIGNEE_COLORS = ("#3b82f6", "#14b8a6", "#22c55e", "#f97316", "#ef4444", "#8b5cf6", "#f59e0b", "#ec4899",
                   "#64748b", "#6366f1", "#06b6d4", "#84cc16")
TASK_COLOR, MILESTONE_COLOR, GROUP_COLOR = "blue", "teal", "slate"
_FLAG_TRUE = {"1", "-1", "true", "yes", "да"}
_ISSUE_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,49}-\d{1,15})(?:[/?#].*)?$")

DEFAULT_OPTIONS = {
    "durations": "dates",       # «dates» — по датам файла; «working» — по длительности в рабочих днях
    "baseline": "current",      # «current» — текущие сроки; «baseline» — сроки базового плана 0 и сдвиг
    "categoryField": None,      # FieldID (или имя, псевдоним) настраиваемого поля задач → категория строки
    "users": (),                # [{id, name, email}] — сотрудники для связи с ресурсами
    "jiraBaseUrls": (),         # адреса Jira: гиперссылка <адрес>/browse/KEY → ссылка Jira строки
    "skipInactive": True,       # неактивные задачи не переносить
    "calendar": None,           # {holidays, workdays} производственного календаря «Пульса»; None — встроенный
}

# Предупреждения в порядке показа: код → текст
WARNINGS = {
    "no_tasks": "В файле нет задач",
    "inactive_tasks": "Неактивные задачи не перенесены",
    "null_tasks": "Пустые строки пропущены",
    "external_tasks": "Внешние задачи (из других проектов) не перенесены",
    "invalid_dates": "Задачи без дат начала и окончания не перенесены",
    "empty_names": "Строки без названия названы «Без названия»",
    "subprojects": "Вставленные проекты перенесены группами без их задач",
    "recurring_tasks": "Повторяющиеся задачи перенесены группами с вхождениями",
    "cross_project_links": "Связи с задачами других проектов не перенесены",
    "links_to_skipped": "Связи с пропущенными строками не перенесены",
    "summary_links": "Связи суммарных задач перенесены на вложенные задачи",
    "summary_links_skipped": "Связи суммарных задач без вложенных задач не перенесены",
    "duplicate_links": "Повторяющиеся связи объединены",
    "cyclic_links": "Связи, замыкающие цикл, не перенесены",
    "lags_adjusted": "Лаги связей подобраны так, чтобы сроки совпали с файлом: MS Project считает связи в часах, "
                     "«Пульс» — в днях",
    "lags_clamped": f"Лаги длиннее {MAX_LAG} дней ограничены",
    "constraints_info": "Ограничения, кроме «Как можно раньше» и «Начало не ранее», перенесены как сведения: "
                        "на расчёт сроков не влияют",
    "group_constraints": "Крайние сроки и ограничения суммарных задач не перенесены",
    "constraints_without_date": "Ограничения без даты не перенесены",
    "constraints_adjusted": "Дата «Начало не ранее» позже начала задачи в файле — взята дата начала",
    "multiple_assignments": "Несколько назначений: исполнитель строки — с наибольшими трудозатратами, остальные — "
                            "в заметке",
    "group_assignments": "Назначения суммарных задач не перенесены",
    "material_resources": "Материальные и затратные ресурсы не перенесены",
    "ambiguous_users": "Несколько сотрудников с одним ФИО — исполнитель не связан с сотрудником",
    "hyperlinks_in_notes": "Ссылки не на задачи Jira перенесены в заметку строки",
    "category_field_empty": "В выбранном настраиваемом поле нет значений — категории не заданы",
    "priority_not_transferred": "Приоритет задач не переносится",
    "costs_not_transferred": "Затраты задач не переносятся",
    "task_calendars_not_transferred": "Календари задач не переносятся: сроки — по датам из файла",
    "calendar_exceptions_unsupported": "Исключения календаря с нераспознанным повторением не перенесены",
    "calendar_exceptions_truncated": "Исключений календаря слишком много — перенесены не все",
    "range_too_long": "Сроки строк дальше 15 лет друг от друга: шкала плана ограничена 15 годами",
    "no_baseline": "У задач нет базового плана 0 — взяты текущие сроки",
    "working_dates_differ": "По производственному календарю «Пульса» окончание задач в рабочих днях разойдётся "
                            "с файлом",
    "too_many_items": f"В плане больше {ITEM_LIMIT} строк — работа с ним может замедлиться",
}


class _Warnings:
    def __init__(self):
        self.data = {}

    def add(self, code, example=None):
        entry = self.data.setdefault(code, {"count": 0, "examples": []})
        entry["count"] += 1
        if example and len(entry["examples"]) < 5 and example not in entry["examples"]:
            entry["examples"].append(example)

    def count(self, code):
        return self.data.get(code, {}).get("count", 0)

    def as_list(self):
        return [{"code": code, "message": message, "count": self.data[code]["count"],
                 "examples": list(self.data[code]["examples"])}
                for code, message in WARNINGS.items() if code in self.data]


# ---------------------------------------------------------------- помощники
def round_half(value):
    """Округление до целого «половина — от нуля» (0,5 → 1, −0,5 → −1), а не банковское."""
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


def _iso(value):
    return value.isoformat() if value else None


def _hours(minutes):
    hours = round((minutes or 0) / 60, 1)
    text = f"{hours:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def normalize_options(options=None):
    """Параметры загрузки с проверкой; неизвестные ключи игнорируются."""
    raw = dict(options or {})
    result = dict(DEFAULT_OPTIONS)
    durations = raw.get("durations") or "dates"
    if durations not in ("dates", "working"):
        raise MsProjectError("Параметр «Длительности»: «dates» или «working»")
    baseline = raw.get("baseline") or "current"
    if baseline not in ("current", "baseline"):
        raise MsProjectError("Параметр «Сроки»: «current» или «baseline»")
    field = raw.get("categoryField")
    users = []
    for user in raw.get("users") or ():
        if isinstance(user, dict) and user.get("id") is not None:
            users.append({"id": user["id"], "name": str(user.get("name") or ""), "email": str(user.get("email") or "")})
    bases = []
    for url in raw.get("jiraBaseUrls") or ():
        if isinstance(url, str) and re.match(r"^https?://", url.strip(), re.IGNORECASE):
            bases.append(url.strip().rstrip("/"))
    calendar = raw.get("calendar")
    result.update({
        "durations": durations, "baseline": baseline,
        "categoryField": str(field).strip() if field not in (None, "") else None,
        "users": users, "jiraBaseUrls": bases, "skipInactive": raw.get("skipInactive", True) is not False,
        "calendar": calendar if isinstance(calendar, dict) else None,
    })
    return result


def jira_url(address, bases):
    """Гиперссылка на задачу подключённой Jira → '<адрес>/browse/KEY'; иначе None."""
    if not address or not bases:
        return None
    text = address.strip()
    for base in bases:
        prefix = base + "/browse/"
        if text.lower().startswith(prefix.lower()):
            match = _ISSUE_KEY.match(text[len(prefix):])
            if match:
                return prefix + match.group(1).upper()
    return None


class _Context:
    """Общие для правил сведения о файле: календарь проекта, начало рабочего дня, минут в рабочем дне."""

    def __init__(self, project, options):
        self.project = project
        self.options = options
        self.minutes_per_day = project.minutes_per_day or 480
        calendar = project.calendar
        self.calendar = calendar
        self.week = project.week_days(calendar) if calendar is not None else {}
        starts = [t.start for day in self.week.values() if day.working for t in day.times]
        self.day_start = min(starts) if starts else (project.default_start_time or DEFAULT_DAY_START)

    def finish_day(self, moment):
        """Окончание в начале рабочего дня и раньше (08:00, 00:00) — это конец предыдущего дня."""
        if moment is None:
            return None
        day = moment.date()
        return day - timedelta(days=1) if moment.time() <= self.day_start else day

    def start_day(self, moment):
        return moment.date() if moment is not None else None

    def lag(self, link, predecessor):
        return convert_lag(link, predecessor.duration if predecessor is not None else None, self.minutes_per_day)


def convert_lag(link, predecessor_minutes, minutes_per_day):
    """Лаг связи MS Project → (дни, вид дней) по правилам «Пульса» (окончание предшественника — конец дня).

    Рабочие единицы — рабочие дни (минуты / минут в дне), прошедшие — календарные (минуты / 1440), проценты —
    доля длительности предшественника в рабочих днях. Дробная часть — как в MS Project при днях целиком:
    FS и SS — вниз (+4ч после окончания в 17:00 — это следующее утро, лаг 0; −4ч — тот же день, лаг −1),
    FF — вверх (окончание +4ч — следующий день), SF — вверх и на день раньше: в «Пульсе» SF кончает задачу в день
    начала предшественника, в MS Project — к его началу, то есть в конце предыдущего рабочего дня."""
    fmt = base_format(link.lag_format) or FORMAT_DAYS
    if fmt in PERCENT_FORMATS:
        minutes, unit, kind = (predecessor_minutes or 0) * link.lag_tenths / 100, minutes_per_day, "working"
    elif fmt in ELAPSED_FORMATS:
        minutes, unit, kind = link.lag_tenths / 10, 1440, "calendar"
    else:
        minutes, unit, kind = link.lag_tenths / 10, minutes_per_day, "working"
    value = minutes / unit
    if link.type_code in ("FS", "SS"):
        days = math.floor(value + 1e-9)
    elif kind == "working":
        days = math.ceil(value - 1e-9)
    else:
        days = round_half(value)
    if link.type_code == "SF":
        days -= 1
    return days, kind


# ---------------------------------------------------------------- сопоставление
def map_project(project, options=None):
    """Модель MS Project + параметры загрузки → документ загрузки плана «Пульса» (JSON-совместимый словарь).

    Неожиданное значение в файле (дата за пределами календаря, длительность в миллионы дней) — MsProjectError
    с текстом для сотрудника, а не ошибка сервера."""
    opts = normalize_options(options)
    try:
        return _map(project, opts)
    except MsProjectError:
        raise
    except (OverflowError, ValueError, ArithmeticError) as error:
        raise MsProjectError("В файле MS Project недопустимые значения (даты или длительности) — проверьте "
                             f"файл в MS Project и сохраните его заново ({type(error).__name__})") from None


def _map(project, opts):
    ctx = _Context(project, opts)
    warn = _Warnings()
    by_uid = {}
    for task in project.tasks:
        by_uid.setdefault(task.uid, task)

    rows, parent_of = _select_rows(project, opts, warn)
    children = {}
    for task in rows:
        children.setdefault(parent_of[task.uid], []).append(task.uid)

    # Тип строки; задачи без дат (их не бывает в файлах MS Project, но бывают у сторонних программ) — пропуск
    kinds, final = {}, []
    for task in rows:
        if task.summary or task.uid in children:
            kinds[task.uid] = "group"
        elif (task.start or task.manual_start) is None or (task.finish or task.manual_finish) is None:
            warn.add("invalid_dates", task.name or str(task.uid))
            continue
        elif task.milestone or (task.duration is not None and abs(task.duration) < 1e-9):
            kinds[task.uid] = "milestone"
        else:
            kinds[task.uid] = "task"
        final.append(task)
    rows = final

    category = _CategoryField(project, opts["categoryField"])
    custom = opts["calendar"]
    ru_calendar = RuCalendar(custom.get("holidays"), custom.get("workdays")) if custom else RuCalendar()
    working = ru_calendar if opts["durations"] == "working" else None

    items, notes_extra, baseline_items = [], {}, {}
    keys = {task.uid: f"i{task.uid}" for task in rows}
    for index, task in enumerate(rows):
        item = _item(task, kinds[task.uid], keys, parent_of, index, ctx, warn, category, working, notes_extra)
        items.append(item)
        if 0 in task.baselines and kinds[task.uid] != "group":
            base = task.baselines[0]
            if base.start is not None and base.finish is not None:
                start = ctx.start_day(base.start)
                baseline_items[item["key"]] = (
                    {"date": _iso(start)} if item["type"] == "milestone"
                    else {"start": _iso(start), "end": _iso(max(start, ctx.finish_day(base.finish)))})
    item_by_key = {item["key"]: item for item in items}

    links = _links(project, by_uid, kinds, keys, children, item_by_key, rows, ctx, warn)
    for item in clamp_snet(items):
        warn.add("constraints_adjusted", item["name"])
    if opts["baseline"] == "current":
        # Даты файла — главные: лаги подбираются так, чтобы автосдвиг «Пульса» их не трогал. По базовому плану
        # сроки строк — плановые, а предшественники — со сдвигом: там расчёт и должен показать отставание.
        names = {item["key"]: item["name"] for item in items}
        for link, _ in align_links(items, links, ru_calendar):
            warn.add("lags_adjusted", f"{names[link['from']]} → {names[link['to']]}")
    assignees, assignee_of, also = _assignees(project, rows, kinds, keys, opts, warn)
    for item in items:
        uid = item["extra"]["msp"]["uid"]
        if item["type"] != "group" and uid in assignee_of:
            item["assigneeKey"] = assignee_of[uid]

    comments = []
    for task in rows:
        parts = []
        text = plain_text(task.notes)
        if text:
            parts.append(text)
        parts.extend(notes_extra.get(task.uid, ()))
        if also.get(task.uid):
            parts.append("Также назначены: " + ", ".join(also[task.uid]))
        if parts:
            comments.append({"itemKey": keys[task.uid], "text": (MS_NOTE_PREFIX + "\n".join(parts))[:TEXT_LIMIT]})

    if category.field_id is not None and not category.values:
        warn.add("category_field_empty")
    if not items:
        warn.add("no_tasks")
    if len(items) > ITEM_LIMIT:
        warn.add("too_many_items")
    project_start, project_finish, plan_range = _range(project, items, ctx, warn)
    calendar = _calendar_info(ctx, plan_range, warn)

    stats = {
        "groups": sum(1 for i in items if i["type"] == "group"),
        "tasks": sum(1 for i in items if i["type"] == "task"),
        "milestones": sum(1 for i in items if i["type"] == "milestone"),
        "links": len(links),
        "assignees": len(assignees),
        "linkedUsers": sum(1 for a in assignees if a["userId"] is not None),
        "comments": len(comments),
        "categories": len(category.values),
        "skipped": {
            "inactive": warn.count("inactive_tasks"), "null": warn.count("null_tasks"),
            "external": warn.count("external_tasks"), "invalidDates": warn.count("invalid_dates"),
            "crossProjectLinks": warn.count("cross_project_links"), "linksToSkipped": warn.count("links_to_skipped"),
            "summaryLinks": warn.count("summary_links_skipped"), "cyclicLinks": warn.count("cyclic_links"),
            "materialResources": warn.count("material_resources"),
        },
    }
    name = clean(project.title or _summary_name(project) or _stem(project.name) or "").strip() or "Проект из MS Project"
    return {
        "project": {"name": name[:500], "title": clean(project.title) or None, "guid": project.guid,
                    "start": _iso(project_start), "finish": _iso(project_finish)},
        "settings": {
            "range": plan_range,
            "categories": list(category.values),
            "source": {"kind": "msproject", "projectGuid": project.guid},
        },
        "items": items,
        "links": links,
        "assignees": assignees,
        "comments": comments,
        "calendar": calendar,
        # Календарь «Пульса», по которому подобраны лаги в рабочих днях (тот же, что у плана — calendar_days)
        "pulsCalendar": ru_calendar.export(),
        "baseline": {"number": 0, "items": baseline_items} if baseline_items else None,
        "customFields": _custom_fields(project, rows),
        "options": {key: opts[key] for key in ("durations", "baseline", "categoryField", "skipInactive")},
        "warnings": warn.as_list(),
        "stats": stats,
    }


def _summary_name(project):
    for task in project.tasks:
        if task.uid == 0 or task.outline_level == 0:
            return task.name or None
    return None


def _stem(name):
    if not name:
        return None
    return re.sub(r"\.(xml|mpp|mpx)$", "", name.strip(), flags=re.IGNORECASE) or None


def _select_rows(project, opts, warn):
    """Строки плана в порядке файла и их родители — ближайшие перенесённые предки по уровню структуры.
    Неактивные (с параметром skipInactive), пустые и внешние задачи пропускаются; вложенные в пропущенную
    суммарную задачу (в MS Project они тоже неактивны) поднимаются к её родителю."""
    rows, parent_of, raw_parent, skipped = [], {}, {}, {}
    stack = []
    for task in project.tasks:
        if task.uid == 0 or task.outline_level == 0:
            continue
        if task.is_null:
            skipped[task.uid] = "null"
            warn.add("null_tasks", f"№ {task.id}" if task.id is not None else None)
            continue
        level = task.outline_level
        if level is None:
            level = task.outline_number.count(".") + 1 if task.outline_number else 1
        while stack and stack[-1][0] >= level:
            stack.pop()
        raw_parent[task.uid] = stack[-1][1] if stack else None
        stack.append((level, task.uid))
        reason = None
        if task.external:
            reason = "external_tasks"
        elif not task.active and opts["skipInactive"]:
            reason = "inactive_tasks"
        if reason:
            skipped[task.uid] = reason
            warn.add(reason, task.name or None)
            continue
        parent = raw_parent[task.uid]
        while parent is not None and parent in skipped:
            parent = raw_parent.get(parent)
        parent_of[task.uid] = parent
        rows.append(task)
    return rows, parent_of


class _CategoryField:
    """Настраиваемое поле — категория строки: текст — значение, флаг — название поля, если флаг установлен."""

    def __init__(self, project, wanted):
        self.field_id = None
        self.flag_label = None
        self.values = []
        self._index = {}
        if not wanted:
            return
        definitions = project.extended_attributes
        definition = definitions.get(wanted) or next(
            (d for d in definitions.values() if wanted in (d.field_name, d.alias)), None)
        self.field_id = definition.field_id if definition else wanted
        if definition and (definition.field_name or "").lower().startswith("flag"):
            self.flag_label = definition.label

    def value(self, task):
        if self.field_id is None:
            return None
        raw = task.extended.get(self.field_id)
        if raw is None:
            return None
        if self.flag_label is not None:
            value = self.flag_label if raw.strip().lower() in _FLAG_TRUE else None
        else:
            value = " ".join(raw.split())[:255] or None
        if value and value not in self._index:
            self._index[value] = len(self.values)
            self.values.append(value)
        return value

    def color(self, value):
        return CATEGORY_COLORS[self._index[value] % len(CATEGORY_COLORS)]


def _item(task, kind, keys, parent_of, index, ctx, warn, category, ru_calendar, notes_extra):
    opts = ctx.options
    name = " ".join(clean(task.name).split()) if task.name else ""
    if not name:
        name = NO_NAME
        warn.add("empty_names", f"№ {task.id}" if task.id is not None else None)
    parent = parent_of.get(task.uid)
    item = {
        "key": keys[task.uid], "type": kind, "name": name[:TEXT_LIMIT],
        "parentKey": keys[parent] if parent is not None else None, "position": 1024.0 * (index + 1),
    }
    if task.recurring and kind == "group":
        warn.add("recurring_tasks", name)
    if task.is_subproject:
        warn.add("subprojects", name)
    if task.priority is not None and task.priority != 500:
        warn.add("priority_not_transferred", name)
    if (task.cost or 0) > 0 or (task.fixed_cost or 0) > 0:
        warn.add("costs_not_transferred", name)
    if task.calendar_uid not in (None, -1) and kind != "group":
        warn.add("task_calendars_not_transferred", name)

    jira = jira_url(task.hyperlink_address, opts["jiraBaseUrls"])
    if task.hyperlink_address and not jira:
        label = clean(task.hyperlink) if task.hyperlink and task.hyperlink != task.hyperlink_address else None
        address = clean(task.hyperlink_address)
        notes_extra.setdefault(task.uid, []).append(f"Ссылка: {label} — {address}" if label else f"Ссылка: {address}")
        warn.add("hyperlinks_in_notes", name)

    constraint = task.constraint
    if kind == "group":
        if task.deadline is not None or constraint:
            warn.add("group_constraints", name)
        item.update({"color": GROUP_COLOR, "jiraUrl": jira})
    else:
        if constraint and constraint != "alap" and task.constraint_date is None:
            # «Начало не ранее» без даты сервер не примет (а в MS Project такого не бывает) — ограничения нет
            warn.add("constraints_without_date", name)
            constraint = None
        if constraint not in (None, "snet"):
            warn.add("constraints_info", name)
        if constraint and constraint != "alap":
            constraint_date = (ctx.finish_day(task.constraint_date) if constraint in FINISH_CONSTRAINTS
                               else task.constraint_date.date())
        else:
            constraint_date = None
        common = {"assigneeKey": None, "jiraUrl": jira, "deadline": _iso(ctx.finish_day(task.deadline)),
                  "constraintType": constraint, "constraintDate": _iso(constraint_date)}
        start_moment = task.start or task.manual_start
        finish_moment = task.finish or task.manual_finish
        base = task.baselines.get(0) if opts["baseline"] == "baseline" else None
        if opts["baseline"] == "baseline" and (base is None or base.start is None or base.finish is None):
            warn.add("no_baseline", name)
            base = None
        if kind == "milestone":
            day = ctx.start_day(base.start if base else start_moment)
            item.update({"date": _iso(day), "color": MILESTONE_COLOR, **common})
        else:
            current_start = ctx.start_day(start_moment)
            current_end = max(current_start, ctx.finish_day(finish_moment))
            if base:
                start = ctx.start_day(base.start)
                end = max(start, ctx.finish_day(base.finish))
                offset = (current_end - end).days
                minutes = base.duration if base.duration is not None else task.duration
            else:
                start, end, offset, minutes = current_start, current_end, 0, task.duration
            progress = task.percent_complete
            status = "done" if progress >= 100 else "inwork" if progress > 0 else "new"
            completed = None
            if status == "done":
                completed = ctx.finish_day(task.actual_finish or finish_moment)
            value = clean(category.value(task)) or None
            item.update({
                "status": status, "start": _iso(start), "end": _iso(end), "endMode": "date", "duration": None,
                "durationType": "calendar", "progress": progress, "offset": offset, "completedDate": _iso(completed),
                "category": value, "color": category.color(value) if value else TASK_COLOR, **common,
            })
            if ru_calendar is not None and minutes is not None:
                _working_duration(item, task, minutes, start, end, ctx, ru_calendar, warn)
    item["extra"] = {"msp": {"uid": task.uid, "guid": task.guid, "wbs": task.wbs, "outlineNumber": task.outline_number}}
    return item


def _working_duration(item, task, minutes, start, end, ctx, ru_calendar, warn):
    """Параметр «Длительности в рабочих днях»: задача — «по длительности»; прошедшая длительность («5ед») —
    в календарных днях. Если по календарю «Пульса» окончание выйдет другим — предупреждение."""
    elapsed = base_format(task.duration_format) in ELAPSED_FORMATS
    days = max(1, round_half(minutes / (1440 if elapsed else ctx.minutes_per_day)))
    if days > MAX_DURATION:
        # Длительность не сходится с датами на столетия — задача остаётся «по дате»
        warn.add("working_dates_differ", item["name"])
        return
    if elapsed:
        item.update({"endMode": "duration", "duration": days, "durationType": "calendar"})
        expected = start + timedelta(days=days - 1)
    else:
        item.update({"endMode": "duration", "duration": days, "durationType": "working"})
        expected = ru_calendar.add_working_days(start, days)
    if expected != end:
        warn.add("working_dates_differ", item["name"])


def _descendant_rows(uid, children, kinds):
    out, stack = [], list(reversed(children.get(uid, [])))
    while stack:
        current = stack.pop()
        if kinds.get(current) == "group":
            stack.extend(reversed(children.get(current, [])))
        elif current in kinds:
            out.append(current)
    return out


def _links(project, by_uid, kinds, keys, children, item_by_key, rows, ctx, warn):
    order = {task.uid: n for n, task in enumerate(rows)}

    def name_of(uid):
        task = by_uid.get(uid)
        return (task.name or f"№ {task.id}") if task is not None else f"UID {uid}"

    def bounds(uid):
        item = item_by_key[keys[uid]]
        if item["type"] == "milestone":
            return item["date"], item["date"]
        return item["start"], item["end"]

    picked = {}

    def pick(group_uid, use_finish):
        """Вложенная задача вместо суммарной: по окончанию — самая поздняя (при равенстве — последняя), по
        началу — самая ранняя (при равенстве — первая)."""
        if (group_uid, use_finish) in picked:
            return picked[group_uid, use_finish]
        candidates = _descendant_rows(group_uid, children, kinds)
        if not candidates:
            result = None
        elif use_finish:
            result = max(candidates, key=lambda uid: (bounds(uid)[1], order[uid]))
        else:
            result = min(candidates, key=lambda uid: (bounds(uid)[0], order[uid]))
        picked[group_uid, use_finish] = result
        return result

    candidates, seen = [], set()
    for link in project.links:
        pred, succ = link.predecessor_uid, link.successor_uid
        label = f"{name_of(pred)} → {name_of(succ)}"
        pred_task, succ_task = by_uid.get(pred), by_uid.get(succ)
        if link.cross_project or (pred_task is not None and pred_task.external) or (
                succ_task is not None and succ_task.external):
            warn.add("cross_project_links", label)
            continue
        if pred not in kinds or succ not in kinds:
            warn.add("links_to_skipped", label)
            continue
        kind = link.type_code
        lag, lag_kind = ctx.lag(link, pred_task)
        if abs(lag) > MAX_LAG:
            warn.add("lags_clamped", label)
            lag = max(-MAX_LAG, min(MAX_LAG, lag))
        source, target, moved = pred, succ, False
        if kinds[pred] == "group":
            source, moved = pick(pred, kind in ("FS", "FF")), True
        if kinds[succ] == "group":
            target, moved = pick(succ, kind in ("FF", "SF")), True
        if source is None or target is None or source == target:
            warn.add("summary_links_skipped", label)
            continue
        if moved:
            warn.add("summary_links", label)
        if (source, target) in seen:
            warn.add("duplicate_links", label)
            continue
        seen.add((source, target))
        candidates.append({"key": f"l{source}-{target}", "from": keys[source], "to": keys[target], "type": kind,
                           "lag": lag, "lagKind": lag_kind})
    return _drop_cycles(candidates, warn, {keys[uid]: name_of(uid) for uid in kinds})


def _drop_cycles(links, warn, names):
    """Связи, замыкающие цикл, отбрасываются (в порядке файла: остаётся первая). Сильно связные компоненты —
    за один проход (Тарьян); проверка пути — только внутри компоненты, где цикл возможен."""
    graph = {}
    for link in links:
        graph.setdefault(link["from"], []).append(link["to"])
    component = _components(graph)
    inner = [link for link in links if component.get(link["from"]) is not None
             and component.get(link["from"]) == component.get(link["to"])]
    if not inner:
        return links
    inner_ids = {id(link) for link in inner}
    adjacency = {}
    for link in links:
        if id(link) not in inner_ids:
            adjacency.setdefault(link["from"], set()).add(link["to"])
    dropped = set()
    for link in inner:
        members = component[link["from"]]
        if _reaches(adjacency, link["to"], link["from"], members, component):
            dropped.add(id(link))
            warn.add("cyclic_links", f"{names.get(link['from'], link['from'])} → {names.get(link['to'], link['to'])}")
            continue
        adjacency.setdefault(link["from"], set()).add(link["to"])
    return [link for link in links if id(link) not in dropped]


def _reaches(adjacency, start, goal, members, component):
    stack, seen = [start], {start}
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        for nxt in adjacency.get(node, ()):
            if nxt not in seen and component.get(nxt) == members:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _components(graph):
    """Номер сильно связной компоненты для вершин в циклах (итеративный алгоритм Тарьяна)."""
    index, low, on_stack, stack, result = {}, {}, set(), [], {}
    counter = 0
    nodes = list(graph)
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(graph.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph.get(child, ()))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    members.append(top)
                    if top == node:
                        break
                if len(members) > 1 or node in graph.get(node, ()):
                    for member in members:
                        result[member] = node
    return result


def _assignees(project, rows, kinds, keys, opts, warn):
    """Ресурсы-работники → исполнители плана; назначения → исполнитель строки и «Также назначены»."""
    by_email, by_name = {}, {}
    for user in opts["users"]:
        if user["email"]:
            by_email.setdefault(norm_email(user["email"]), []).append(user["id"])
        if user["name"]:
            by_name.setdefault(norm_name(user["name"]), []).append(user["id"])
    assignees, resource_key, resource_name = [], {}, {}
    for resource in project.resources:
        name = " ".join(clean(resource.name).split())
        if resource.is_null or not name:
            continue
        if resource.type != RESOURCE_WORK:
            warn.add("material_resources", name)
            continue
        user_id = None
        emails = by_email.get(norm_email(resource.email)) if resource.email else None
        names = by_name.get(norm_name(name))
        if emails and len(set(emails)) == 1:
            user_id = emails[0]
        elif names and len(set(names)) == 1:
            user_id = names[0]
        elif names:
            warn.add("ambiguous_users", name)
        key = f"a{resource.uid}"
        resource_key[resource.uid] = key
        resource_name[resource.uid] = name
        assignees.append({"key": key, "name": name[:500], "role": " ".join(clean(resource.group).split())[:500],
                          "color": ASSIGNEE_COLORS[len(assignees) % len(ASSIGNEE_COLORS)], "userId": user_id,
                          "position": float(len(assignees) + 1)})
    by_task = {}
    for n, assignment in enumerate(project.assignments):
        if assignment.resource_uid in resource_key:
            by_task.setdefault(assignment.task_uid, []).append((n, assignment))
    assignee_of, also = {}, {}
    for task in rows:
        work = by_task.get(task.uid)
        if not work:
            continue
        if kinds[task.uid] == "group":
            warn.add("group_assignments", task.name or None)
            continue
        ordered = sorted(work, key=lambda pair: (-(pair[1].work or 0), -(pair[1].units or 0), pair[0]))
        main = ordered[0][1]
        assignee_of[task.uid] = resource_key[main.resource_uid]
        others = []
        for _, assignment in ordered[1:]:
            if assignment.resource_uid == main.resource_uid:
                continue
            label = resource_name[assignment.resource_uid]
            others.append(f"{label} ({_hours(assignment.work)} ч)" if assignment.work else label)
        if others:
            also[task.uid] = list(dict.fromkeys(others))
            warn.add("multiple_assignments", task.name or None)
    return assignees, assignee_of, also


def _range(project, items, ctx, warn):
    """Шкала плана: начало проекта − 7 дней … окончание + 14 дней (и все даты строк), не длиннее 15 лет — иначе
    план не создать. Если строки разбросаны шире, шкала — по срокам проекта, а если и они шире — 15 лет от
    начала проекта."""
    days = []
    for item in items:
        for name in ("start", "end", "date"):
            if item.get(name):
                days.append(date.fromisoformat(item[name]))
        if item.get("end") and item.get("offset"):
            days.append(date.fromisoformat(item["end"]) + timedelta(days=item["offset"]))
    start = project.start.date() if project.start else (min(days) if days else None)
    finish = ctx.finish_day(project.finish) if project.finish else (max(days) if days else None)
    if start is None:
        start = finish = date.today()
    if finish is None or finish < start:
        finish = start
    low = min([start] + days) - timedelta(days=7)
    high = max([finish] + days) + timedelta(days=14)
    if (high - low).days > RANGE_LIMIT:
        warn.add("range_too_long")
        low, high = start - timedelta(days=7), finish + timedelta(days=14)
        if (high - low).days > RANGE_LIMIT:
            high = low + timedelta(days=RANGE_LIMIT)
    return start, finish, {"start": low.isoformat(), "end": high.isoformat()}


def _calendar_info(ctx, plan_range, warn, limit=3660):
    """Календарь проекта для плана загрузки (и для календаря плана, раздел 5.6): рабочая неделя (1 — пн …
    7 — вс) и исключения в пределах шкалы плана ± год."""
    calendar = ctx.calendar
    if calendar is None:
        return None
    week = sorted(DAY_TYPE_TO_ISO[day_type] for day_type, day in ctx.week.items() if day.working)
    low = date.fromisoformat(plan_range["start"]) - timedelta(days=366)
    high = date.fromisoformat(plan_range["end"]) + timedelta(days=366)
    exceptions, budget = {}, EXCEPTION_BUDGET
    for exception in calendar.exceptions:
        if budget <= 0 or len(exceptions) >= limit:
            warn.add("calendar_exceptions_truncated")
            break
        dates, understood = exception.dates(limit=min(limit, budget), low=low, high=high)
        budget -= max(1, len(dates))
        if not understood:
            warn.add("calendar_exceptions_unsupported", clean(exception.name) or None)
            continue
        for day in dates:
            if day not in exceptions:
                exceptions[day] = {"date": day.isoformat(), "working": exception.working,
                                   "title": clean(exception.name)}
    return {
        "name": clean(calendar.name) or None,
        "workWeek": week,
        "dayStart": ctx.day_start.strftime("%H:%M"),
        "minutesPerDay": ctx.minutes_per_day,
        "exceptions": [exceptions[day] for day in sorted(exceptions)][:limit],
    }


def _custom_fields(project, rows):
    """Настраиваемые поля задач для выбора категории: определение из файла и число строк со значением."""
    counts = {}
    for task in rows:
        for field_id, value in task.extended.items():
            if value:
                counts[field_id] = counts.get(field_id, 0) + 1
    fields = []
    seen = set()
    for definition in project.extended_attributes.values():
        if not is_task_field(definition.field_id):
            continue
        seen.add(definition.field_id)
        fields.append({"fieldId": definition.field_id, "fieldName": definition.field_name, "alias": definition.alias,
                       "label": definition.label, "count": counts.get(definition.field_id, 0)})
    for field_id in sorted(set(counts) - seen):
        if is_task_field(field_id):
            fields.append({"fieldId": field_id, "fieldName": None, "alias": None, "label": field_id,
                           "count": counts[field_id]})
    return fields

