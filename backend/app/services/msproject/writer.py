"""Экспорт плана «Пульса» в MS Project XML (MSPDI) (ADR-0012, раздел 5.9).

План открывается в MS Project 2010+ («Файл → Открыть», тип «XML»): группы и задачи с подзадачами — суммарные
задачи, задачи и вехи — с датами, прогрессом и фактом, все связи с лагами, исполнители — ресурсы-работники с
назначениями, крайние сроки и ограничения, категория — настраиваемое поле «Текст1», ссылка Jira — гиперссылка,
комментарии — заметки. Календарь проекта — пятидневка 08:00–12:00, 13:00–17:00 с праздниками и рабочими
выходными, переданными параметрами (производственный календарь «Пульса»).

MS Project проверяет файл по схеме `mspdi_pj15.xsd`, в которой порядок элементов задан: поэтому каждый элемент
пишется по списку из схемы (`*_ORDER` ниже), а неизвестное имя — ошибка программы, а не битый файл.

UID и GUID строк, пришедших из MS Project (`extra.msp`), сохраняются — файл ложится на исходный проект без
дублей; остальным UID выдаются по порядку, GUID — uuid5 от ключа плана и ключа строки (повторный экспорт даёт тот
же файл). Задачи по умолчанию пишутся с ручным планированием: MS Project не пересчитывает их сроки при открытии,
и график совпадает с «Пульсом» день в день; `scheduling="auto"` — автопланирование, а даты задач без своего
ограничения закрепляются ограничением «Начало не ранее».
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta

from app.services.msproject.mapping import MS_NOTE_PREFIX
from app.services.msproject.model import CONSTRAINT_CODES, FINISH_CONSTRAINTS, LINK_TYPE_CODES

NAMESPACE = "http://schemas.microsoft.com/project"
DAY_START, DAY_FINISH = time(8, 0), time(17, 0)
WORK_TIMES = ((time(8, 0), time(12, 0)), (time(13, 0), time(17, 0)))
MINUTES_PER_DAY, MINUTES_PER_WEEK, DAYS_PER_MONTH = 480, 2400, 20
CATEGORY_FIELD_ID, CATEGORY_FIELD_NAME, CATEGORY_ALIAS = "188743731", "Text1", "Категория"
GUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_NAMESPACE_UUID = uuid.UUID("0b7c3f4e-2a59-5d0e-9b8e-7075c5f0a012")
# Символы, недопустимые в XML 1.0 (управляющие, кроме табуляции и переводов строки): MS Project не откроет файл
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

# Порядок дочерних элементов по mspdi_pj15.xsd (только те, что пишет «Пульс», в порядке схемы)
PROJECT_ORDER = (
    "SaveVersion", "UID", "Name", "GUID", "Title", "Subject", "Category", "Company", "Manager", "Author",
    "CreationDate", "Revision", "LastSaved", "ScheduleFromStart", "StartDate", "FinishDate", "FYStartDate",
    "CriticalSlackLimit", "CurrencyDigits", "CurrencySymbol", "CurrencyCode", "CurrencySymbolPosition",
    "CalendarUID", "DefaultStartTime", "DefaultFinishTime", "MinutesPerDay", "MinutesPerWeek", "DaysPerMonth",
    "DefaultTaskType", "DefaultFixedCostAccrual", "DefaultStandardRate", "DefaultOvertimeRate", "DurationFormat",
    "WorkFormat", "EditableActualCosts", "HonorConstraints", "EarnedValueMethod", "InsertedProjectsLikeSummary",
    "MultipleCriticalPaths", "NewTasksEffortDriven", "NewTasksEstimated", "SplitsInProgressTasks",
    "SpreadActualCost", "SpreadPercentComplete", "TaskUpdatesResource", "FiscalYearStart", "WeekStartDay",
    "MoveCompletedEndsBack", "MoveRemainingStartsBack", "MoveRemainingStartsForward", "MoveCompletedEndsForward",
    "BaselineForEarnedValue", "AutoAddNewResourcesAndTasks", "StatusDate", "CurrentDate",
    "MicrosoftProjectServerURL", "Autolink", "NewTaskStartDate", "NewTasksAreManual", "DefaultTaskEVMethod",
    "ProjectExternallyEdited", "ExtendedCreationDate", "ActualsInSync", "RemoveFileProperties", "AdminProject",
    "BaselineCalendar", "UpdateManuallyScheduledTasksWhenEditingLinks",
    "KeepTaskOnNearestWorkingTimeWhenMadeAutoScheduled", "OutlineCodes", "WBSMasks", "ExtendedAttributes",
    "Calendars", "Tasks", "Resources", "Assignments",
)
EXTENDED_DEF_ORDER = ("FieldID", "FieldName", "CFType", "Guid", "ElemType", "MaxMultiValues", "UserDef", "Alias")
CALENDAR_ORDER = ("UID", "GUID", "Name", "IsBaseCalendar", "IsBaselineCalendar", "BaseCalendarUID", "WeekDays",
                  "Exceptions", "WorkWeeks")
WEEKDAY_ORDER = ("DayType", "DayWorking", "TimePeriod", "WorkingTimes")
EXCEPTION_ORDER = ("EnteredByOccurrences", "TimePeriod", "Occurrences", "Name", "Type", "Period", "DaysOfWeek",
                   "MonthItem", "MonthPosition", "Month", "MonthDay", "DayWorking", "WorkingTimes")
TASK_ORDER = (
    "UID", "GUID", "ID", "Name", "Active", "Manual", "Type", "IsNull", "CreateDate", "Contact", "WBS", "WBSLevel",
    "OutlineNumber", "OutlineLevel", "Priority", "Start", "Finish", "Duration", "DurationFormat", "Work", "Stop",
    "Resume", "ResumeValid", "EffortDriven", "Recurring", "OverAllocated", "Estimated", "Milestone", "Summary",
    "DisplayAsSummary", "Critical", "IsSubproject", "IsSubprojectReadOnly", "SubprojectName", "ExternalTask",
    "ExternalTaskProject", "EarlyStart", "EarlyFinish", "LateStart", "LateFinish", "StartVariance",
    "FinishVariance", "WorkVariance", "FreeSlack", "TotalSlack", "StartSlack", "FinishSlack", "FixedCost",
    "FixedCostAccrual", "PercentComplete", "PercentWorkComplete", "Cost", "OvertimeCost", "OvertimeWork",
    "ActualStart", "ActualFinish", "ActualDuration", "ActualCost", "ActualOvertimeCost", "ActualWork",
    "ActualOvertimeWork", "RegularWork", "RemainingDuration", "RemainingCost", "RemainingWork",
    "RemainingOvertimeCost", "RemainingOvertimeWork", "ACWP", "CV", "ConstraintType", "CalendarUID",
    "ConstraintDate", "Deadline", "LevelAssignments", "LevelingCanSplit", "LevelingDelay", "LevelingDelayFormat",
    "PreLeveledStart", "PreLeveledFinish", "Hyperlink", "HyperlinkAddress", "HyperlinkSubAddress",
    "IgnoreResourceCalendar", "Notes", "HideBar", "Rollup", "BCWS", "BCWP", "PhysicalPercentComplete",
    "EarnedValueMethod", "PredecessorLink", "ActualWorkProtected", "ActualOvertimeWorkProtected",
    "ExtendedAttribute", "Baseline", "OutlineCode", "IsPublished", "StatusManager", "CommitmentStart",
    "CommitmentFinish", "CommitmentType", "StartText", "FinishText", "DurationText", "ManualStart", "ManualFinish",
    "ManualDuration",
)
LINK_ORDER = ("PredecessorUID", "Type", "CrossProject", "CrossProjectName", "LinkLag", "LagFormat")
TASK_EXTENDED_ORDER = ("FieldID", "Value", "ValueGUID", "DurationFormat")
BASELINE_ORDER = ("Number", "Interim", "Start", "Finish", "Duration", "DurationFormat", "EstimatedDuration", "Work",
                  "Cost", "BCWS", "BCWP")
RESOURCE_ORDER = (
    "UID", "GUID", "ID", "Name", "Type", "IsNull", "Initials", "Phonetics", "NTAccount", "MaterialLabel", "Code",
    "Group", "WorkGroup", "EmailAddress", "Hyperlink", "HyperlinkAddress", "HyperlinkSubAddress", "MaxUnits",
    "PeakUnits", "OverAllocated", "AvailableFrom", "AvailableTo", "Start", "Finish", "CanLevel", "AccrueAt", "Work",
)
ASSIGNMENT_ORDER = (
    "UID", "GUID", "TaskUID", "ResourceUID", "PercentWorkComplete", "ActualCost", "ActualFinish",
    "ActualOvertimeCost", "ActualOvertimeWork", "ActualStart", "ActualWork", "ACWP", "Confirmed", "Cost",
    "CostRateTable", "CostVariance", "CV", "Delay", "Finish", "FinishVariance", "Hyperlink", "HyperlinkAddress",
    "HyperlinkSubAddress", "WorkVariance", "HasFixedRateUnits", "FixedMaterial", "LevelingDelay",
    "LevelingDelayFormat", "LinkedFields", "Milestone", "Notes", "Overallocated", "OvertimeCost", "OvertimeWork",
    "PeakUnits", "RateScale", "RegularWork", "RemainingCost", "RemainingOvertimeCost", "RemainingOvertimeWork",
    "RemainingWork", "ResponsePending", "Start", "Stop", "Resume", "StartVariance", "Summary", "SV", "Units",
    "UpdateNeeded", "VAC", "Work", "WorkContour",
)


def _child(parent, order, values):
    """Дочерние элементы в порядке схемы. values: имя → текст | Element | [Element]; None — не писать."""
    unknown = set(values) - set(order)
    if unknown:
        raise AssertionError(f"Элементов {sorted(unknown)} нет в схеме {parent.tag}")
    for name in order:
        value = values.get(name)
        if value is None:
            continue
        if isinstance(value, list):
            parent.extend(value)
        elif isinstance(value, ET.Element):
            parent.append(value)
        else:
            ET.SubElement(parent, name).text = _INVALID_XML.sub("", str(value))
    return parent


def _element(tag, order, values):
    return _child(ET.Element(tag), order, values)


def _dt(day, at):
    return datetime.combine(day, at).strftime("%Y-%m-%dT%H:%M:%S")


def _duration(minutes):
    minutes = int(round(minutes))
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    return f"{sign}PT{minutes // 60}H{minutes % 60}M0S"


def _bool(value):
    return "1" if value else "0"


def _day(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _days(values):
    """Даты календаря: список дат (строк или date) или словарь дата → название."""
    out = {}
    if isinstance(values, dict):
        pairs = values.items()
    else:
        pairs = ((value, "") for value in values or ())
    for value, title in pairs:
        day = _day(value)
        if day is not None:
            out[day] = str(title or "")
    return out


class _WorkCalendar:
    def __init__(self, holidays, workdays):
        self.holidays = _days(holidays)
        self.workdays = _days(workdays)

    def is_working(self, day):
        if day in self.holidays:
            return False
        if day in self.workdays:
            return True
        return day.isoweekday() < 6

    def working_days(self, start, finish):
        count, day, guard = 0, start, 0
        while day <= finish and guard < 20000:
            if self.is_working(day):
                count += 1
            day += timedelta(days=1)
            guard += 1
        return count


def _guid(*parts):
    return str(uuid.uuid5(_NAMESPACE_UUID, ":".join(str(p) for p in parts))).upper()


def _initials(name):
    return "".join(part[0] for part in name.split()[:3] if part).upper() or None


def _links_of(document, items):
    """Связи документа; документ до 0.7.0 — из полей «предшественник» строк."""
    if isinstance(document.get("links"), list):
        return [link for link in document["links"] if isinstance(link, dict) and not link.get("deleted")]
    out = []
    for item in items:
        if item.get("dependencyKey"):
            out.append({"key": f"l{item['dependencyKey']}-{item['key']}", "from": item["dependencyKey"],
                        "to": item["key"], "type": item.get("dependencyType") or "FS",
                        "lag": item.get("dependencyLag") or 0, "lagKind": "calendar"})
    return out


def write_mspdi(plan_document, *, holidays=(), workdays=(), include_comments=True, baseline=None,
                scheduling="manual", users=None, saved_at=None):
    """План «Пульса» → файл MS Project XML (bytes, UTF-8).

    plan_document — документ загрузки (map_project) или документ плана API: items, links, assignees,
    comments, project/plan, settings. holidays / workdays — праздники и рабочие выходные (даты или словарь дата →
    название). baseline — {ключ строки: {start, end} | {date}} для базового плана 0; без него базовый план
    пишется, если у задач есть сдвиг (плановые сроки — без сдвига, текущее окончание — со сдвигом).
    users — [{id, email}] для email ресурсов. saved_at — время сохранения (по умолчанию не пишется)."""
    if scheduling not in ("manual", "auto"):
        raise ValueError("scheduling: manual или auto")
    calendar = _WorkCalendar(holidays, workdays)
    plan = plan_document.get("plan") if isinstance(plan_document.get("plan"), dict) else {}
    project = plan_document.get("project") if isinstance(plan_document.get("project"), dict) else {}
    settings = plan_document.get("settings") if isinstance(plan_document.get("settings"), dict) else (
        plan.get("settings") or {})
    source = settings.get("source") if isinstance(settings.get("source"), dict) else {}
    title = project.get("name") or plan.get("title") or "План «Пульса»"
    plan_key = plan.get("key") or source.get("projectGuid") or project.get("guid") or title
    project_guid = (source.get("projectGuid") if source.get("kind") == "msproject" else None) or project.get("guid")
    project_guid = project_guid.upper() if project_guid and GUID_RE.match(project_guid) else _guid(plan_key, "project")

    items = [i for i in plan_document.get("items") or []
             if isinstance(i, dict) and i.get("key") and not i.get("deleted")]
    by_key = {item["key"]: item for item in items}
    children = {}
    for item in items:
        parent = item.get("parentKey")
        children.setdefault(parent if parent in by_key and parent != item["key"] else None, []).append(item)
    for siblings in children.values():
        siblings.sort(key=lambda i: (float(i.get("position") or 0), i["key"]))
    ordered, seen = [], set()

    def walk(top, number):
        """Обход дерева в порядке вывода: (строка, уровень структуры, номер структуры)."""
        stack = [(top, 1, number)]
        while stack:
            item, level, label = stack.pop()
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            ordered.append((item, level, label))
            kids = [kid for kid in children.get(item["key"], []) if kid["key"] not in seen]
            for n in range(len(kids), 0, -1):
                stack.append((kids[n - 1], level + 1, f"{label}.{n}"))

    roots = children.get(None, [])
    for n, root in enumerate(roots, start=1):
        walk(root, str(n))
    count = len(roots)
    for item in items:  # строки, замкнутые в цикл родителей, — на верхний уровень
        if item["key"] not in seen:
            count += 1
            walk(item, str(count))

    # UID: из файла MS Project, если он есть и не повторяется; остальным — по порядку
    uids, used = {}, {0}
    for item, _, _ in ordered:
        uid = ((item.get("extra") or {}).get("msp") or {}).get("uid")
        if isinstance(uid, int) and not isinstance(uid, bool) and uid > 0 and uid not in used:
            uids[item["key"]] = uid
            used.add(uid)
    next_uid = max(used) + 1
    for item, _, _ in ordered:
        if item["key"] not in uids:
            uids[item["key"]] = next_uid
            used.add(next_uid)
            next_uid += 1

    # Сроки: задача — начало и окончание со сдвигом; веха — дата; суммарная — по вложенным
    spans = {}
    for item, _, _ in ordered:
        if item.get("type") == "task" and _day(item.get("start")):
            start = _day(item["start"])
            end = max(start, _day(item.get("end")) or start)
            spans[item["key"]] = (start, end, end + timedelta(days=int(item.get("offset") or 0)))
        elif item.get("type") == "milestone" and _day(item.get("date")):
            day = _day(item["date"])
            spans[item["key"]] = (day, day, day)
    dated = list(spans.values())
    project_start = min((s[0] for s in dated), default=date.today())
    project_finish = max((max(s[1], s[2]) for s in dated), default=project_start)

    def summary_span(key):
        starts, finishes, stack, visited = [], [], list(children.get(key, [])), {key}
        while stack:
            node = stack.pop()
            if node["key"] in visited:
                continue
            visited.add(node["key"])
            if node["key"] in spans:
                start, end, shifted = spans[node["key"]]
                starts.append(start)
                finishes.append(max(start, shifted))
            stack.extend(children.get(node["key"], []))
        return (min(starts), max(finishes)) if starts else (project_start, project_start)

    write_baseline = baseline is not None or any(
        item.get("type") == "task" and int(item.get("offset") or 0) != 0 for item in items)
    baseline = baseline or {}

    comments = {}
    if include_comments:
        for comment in plan_document.get("comments") or []:
            text = str(comment.get("text") or "")
            if text.startswith(MS_NOTE_PREFIX):
                text = text[len(MS_NOTE_PREFIX):]
            if text.strip() and comment.get("itemKey") in by_key:
                comments.setdefault(comment["itemKey"], []).append(text.strip())

    links_in = {}
    for link in _links_of(plan_document, items):
        if link.get("from") in uids and link.get("to") in uids and link["from"] != link["to"]:
            links_in.setdefault(link["to"], []).append(link)

    assignees = sorted((a for a in plan_document.get("assignees") or [] if isinstance(a, dict) and a.get("key")
                        and not a.get("deleted")), key=lambda a: (float(a.get("position") or 0), a["key"]))
    resource_uid, used_resources = {}, set()
    for assignee in assignees:
        match = re.match(r"^a(\d+)$", assignee["key"])
        if match and int(match.group(1)) > 0 and int(match.group(1)) not in used_resources:
            resource_uid[assignee["key"]] = int(match.group(1))
            used_resources.add(int(match.group(1)))
    next_resource = max(used_resources, default=0) + 1
    for assignee in assignees:
        if assignee["key"] not in resource_uid:
            resource_uid[assignee["key"]] = next_resource
            used_resources.add(next_resource)
            next_resource += 1
    emails = {u.get("id"): u.get("email") for u in users or () if isinstance(u, dict)}

    root = ET.Element("Project", {"xmlns": NAMESPACE})
    any_category = any(item.get("category") for item in items if item.get("type") == "task")
    extended = None
    if any_category:
        extended = ET.Element("ExtendedAttributes")
        extended.append(_element("ExtendedAttribute", EXTENDED_DEF_ORDER, {
            "FieldID": CATEGORY_FIELD_ID, "FieldName": CATEGORY_FIELD_NAME, "Alias": CATEGORY_ALIAS}))
    stamp = saved_at.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(saved_at, datetime) else None
    _child(root, PROJECT_ORDER, {
        "SaveVersion": 14, "Name": f"{title}.xml", "GUID": project_guid, "Title": title,
        "CreationDate": stamp, "LastSaved": stamp, "ScheduleFromStart": 1,
        "StartDate": _dt(project_start, DAY_START), "FinishDate": _dt(project_finish, DAY_FINISH),
        "FYStartDate": 1, "CriticalSlackLimit": 0, "CurrencyDigits": 2, "CurrencySymbol": "₽", "CurrencyCode": "RUB",
        "CurrencySymbolPosition": 3, "CalendarUID": 1, "DefaultStartTime": "08:00:00",
        "DefaultFinishTime": "17:00:00", "MinutesPerDay": MINUTES_PER_DAY, "MinutesPerWeek": MINUTES_PER_WEEK,
        "DaysPerMonth": DAYS_PER_MONTH, "DefaultTaskType": 1, "DurationFormat": 7, "WorkFormat": 2,
        "HonorConstraints": 0, "WeekStartDay": 1, "NewTasksAreManual": _bool(scheduling == "manual"),
        "ExtendedAttributes": extended, "Calendars": _calendars(calendar),
    })

    tasks = ET.SubElement(root, "Tasks")
    tasks.append(_element("Task", TASK_ORDER, {
        "UID": 0, "GUID": _guid(plan_key, "summary"), "ID": 0, "Name": title, "Active": 1, "Manual": 0, "Type": 1,
        "IsNull": 0, "WBS": 0, "OutlineNumber": 0, "OutlineLevel": 0, "Priority": 500,
        "Start": _dt(project_start, DAY_START), "Finish": _dt(project_finish, DAY_FINISH),
        "Duration": _duration(max(1, calendar.working_days(project_start, project_finish)) * MINUTES_PER_DAY),
        "DurationFormat": 53, "Milestone": 0, "Summary": 1, "ConstraintType": 0, "CalendarUID": -1,
    }))
    assignment_rows = []
    task_minutes = {}
    used_guids = set()
    for task_id, (item, level, number) in enumerate(ordered, start=1):
        key = item["key"]
        msp = (item.get("extra") or {}).get("msp") or {}
        guid = msp.get("guid").upper() if isinstance(msp.get("guid"), str) and GUID_RE.match(msp.get("guid")) else None
        if guid is None or guid in used_guids:  # строка-копия с тем же GUID — свой GUID
            guid = _guid(plan_key, key)
        used_guids.add(guid)
        is_summary = item.get("type") == "group" or bool(children.get(key))
        kind = item.get("type")
        values = {
            "UID": uids[key], "GUID": guid, "ID": task_id,
            "Name": (item.get("name") or "").strip() or "Без названия", "Active": 1,
            "Manual": _bool(scheduling == "manual" and not is_summary), "Type": 1, "IsNull": 0,
            "WBS": msp.get("wbs") or number, "OutlineNumber": number, "OutlineLevel": level, "Priority": 500,
            "Estimated": 0, "Milestone": _bool(kind == "milestone" and not is_summary), "Summary": _bool(is_summary),
            "ConstraintType": 0, "CalendarUID": -1,
        }
        if is_summary:
            start, finish = summary_span(key)
            minutes = max(1, calendar.working_days(start, finish)) * MINUTES_PER_DAY
            values.update({"Start": _dt(start, DAY_START), "Finish": _dt(finish, DAY_FINISH),
                           "Duration": _duration(minutes), "DurationFormat": 7})
        elif kind == "milestone":
            day = spans[key][0] if key in spans else project_start
            minutes = 0
            values.update({"Start": _dt(day, DAY_FINISH), "Finish": _dt(day, DAY_FINISH), "Duration": _duration(0),
                           "DurationFormat": 7})
        else:
            start, end, shifted = spans.get(key, (project_start, project_start, project_start))
            elapsed = item.get("endMode") == "duration" and item.get("durationType") == "calendar"
            if item.get("endMode") == "duration" and isinstance(item.get("duration"), int) and item["duration"] > 0 \
                    and shifted == end:
                minutes = item["duration"] * (1440 if elapsed else MINUTES_PER_DAY)
            elif elapsed:
                minutes = ((shifted - start).days + 1) * 1440
            else:
                minutes = max(1, calendar.working_days(start, shifted)) * MINUTES_PER_DAY
            finish = _dt(start + timedelta(days=minutes // 1440), DAY_START) if elapsed else _dt(shifted, DAY_FINISH)
            values.update({"Start": _dt(start, DAY_START), "Finish": finish, "Duration": _duration(minutes),
                           "DurationFormat": 8 if elapsed else 7})
        task_minutes[key] = minutes
        if not is_summary:
            values.update(_progress(item, kind, spans.get(key)))
            values.update(_constraint(item, scheduling, spans.get(key)))
            if _day(item.get("deadline")):
                values["Deadline"] = _dt(_day(item["deadline"]), DAY_FINISH)
            if values["Manual"] == "1":
                values.update({"ManualStart": values["Start"], "ManualFinish": values["Finish"],
                               "ManualDuration": values["Duration"]})
        if item.get("jiraUrl"):
            values["Hyperlink"] = item["jiraUrl"].rstrip("/").rsplit("/", 1)[-1]
            values["HyperlinkAddress"] = item["jiraUrl"]
        if comments.get(key):
            values["Notes"] = "\n\n".join(comments[key])
        links = links_in.get(key, [])  # в порядке документа: повторный импорт даёт тот же порядок связей
        if links:
            values["PredecessorLink"] = [_link(link, uids) for link in links]
        if kind == "task" and item.get("category") and not is_summary:
            values["ExtendedAttribute"] = [_element("ExtendedAttribute", TASK_EXTENDED_ORDER, {
                "FieldID": CATEGORY_FIELD_ID, "Value": item["category"]})]
        if write_baseline and not is_summary:
            values["Baseline"] = _baseline(item, kind, spans.get(key), baseline.get(key), calendar)
        tasks.append(_element("Task", TASK_ORDER, values))
        if not is_summary and item.get("assigneeKey") in resource_uid:
            assignment_rows.append((item, values))

    resources = ET.SubElement(root, "Resources")
    for resource_id, assignee in enumerate(assignees, start=1):
        name = " ".join(str(assignee.get("name") or "").split()) or f"Исполнитель {resource_id}"
        resources.append(_element("Resource", RESOURCE_ORDER, {
            "UID": resource_uid[assignee["key"]], "GUID": _guid(plan_key, "assignee", assignee["key"]),
            "ID": resource_id, "Name": name, "Type": 1, "IsNull": 0, "Initials": _initials(name),
            "Group": (assignee.get("role") or None), "EmailAddress": emails.get(assignee.get("userId")) or None,
            "MaxUnits": "1.00", "PeakUnits": "1.00", "CanLevel": 1, "AccrueAt": 3,
        }))
    assignments = ET.SubElement(root, "Assignments")
    for assignment_id, (item, values) in enumerate(assignment_rows, start=1):
        assignments.append(_element("Assignment", ASSIGNMENT_ORDER, {
            "UID": assignment_id, "GUID": _guid(plan_key, "assignment", item["key"]), "TaskUID": values["UID"],
            "ResourceUID": resource_uid[item["assigneeKey"]], "Finish": values["Finish"], "Start": values["Start"],
            "Units": "1", "Work": _duration(task_minutes[item["key"]]),
        }))

    ET.indent(root, space="\t")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + "\n").encode("utf-8")


def _calendars(calendar):
    calendars = ET.Element("Calendars")
    week = ET.Element("WeekDays")
    for day_type in range(1, 8):
        working = 2 <= day_type <= 6
        week.append(_element("WeekDay", WEEKDAY_ORDER, {
            "DayType": day_type, "DayWorking": _bool(working), "WorkingTimes": _working_times() if working else None}))
    exceptions = ET.Element("Exceptions")
    days = sorted([(day, False, title) for day, title in calendar.holidays.items()]
                  + [(day, True, title) for day, title in calendar.workdays.items() if day not in calendar.holidays])
    runs = []
    for day, working, title in days:
        if runs and runs[-1][1] + timedelta(days=1) == day and runs[-1][2] == working and runs[-1][3] == title:
            runs[-1][1] = day
        else:
            runs.append([day, day, working, title])
    for start, finish, working, title in runs:
        period = ET.Element("TimePeriod")
        ET.SubElement(period, "FromDate").text = _dt(start, time(0, 0))
        ET.SubElement(period, "ToDate").text = _dt(finish, time(23, 59))
        exceptions.append(_element("Exception", EXCEPTION_ORDER, {
            "EnteredByOccurrences": 0, "TimePeriod": period, "Occurrences": (finish - start).days + 1,
            "Name": title or ("Рабочий день" if working else "Нерабочий день"), "Type": 1, "Period": 1,
            "DayWorking": _bool(working), "WorkingTimes": _working_times() if working else None,
        }))
    calendars.append(_element("Calendar", CALENDAR_ORDER, {
        "UID": 1, "GUID": _guid("calendar", "standard"), "Name": "Стандартный", "IsBaseCalendar": 1,
        "IsBaselineCalendar": 0, "BaseCalendarUID": -1, "WeekDays": week,
        "Exceptions": exceptions if len(exceptions) else None,
    }))
    return calendars


def _working_times():
    times = ET.Element("WorkingTimes")
    for start, finish in WORK_TIMES:
        working = ET.SubElement(times, "WorkingTime")
        ET.SubElement(working, "FromTime").text = start.strftime("%H:%M:%S")
        ET.SubElement(working, "ToTime").text = finish.strftime("%H:%M:%S")
    return times


def _progress(item, kind, span):
    if kind != "task" or span is None:
        return {"PercentComplete": 0}
    start, _, shifted = span
    progress = max(0, min(100, int(round(float(item.get("progress") or 0)))))
    if item.get("status") == "done":
        progress = 100
    values = {"PercentComplete": progress}
    if progress > 0:
        values["ActualStart"] = _dt(start, DAY_START)
    if progress == 100:
        values["ActualFinish"] = _dt(_day(item.get("completedDate")) or shifted, DAY_FINISH)
    return values


def _constraint(item, scheduling, span):
    kind = item.get("constraintType")
    day = _day(item.get("constraintDate"))
    if kind in CONSTRAINT_CODES:
        values = {"ConstraintType": CONSTRAINT_CODES[kind]}
        if day is not None and kind != "alap":
            values["ConstraintDate"] = _dt(day, DAY_FINISH if kind in FINISH_CONSTRAINTS else DAY_START)
        return values
    if scheduling == "auto" and span is not None:
        return {"ConstraintType": CONSTRAINT_CODES["snet"], "ConstraintDate": _dt(span[0], DAY_START)}
    return {}


def _link(link, uids):
    kind = link.get("type") if link.get("type") in LINK_TYPE_CODES else "FS"
    lag = int(link.get("lag") or 0)
    elapsed = link.get("lagKind") == "calendar"
    return _element("PredecessorLink", LINK_ORDER, {
        "PredecessorUID": uids[link["from"]], "Type": LINK_TYPE_CODES[kind], "CrossProject": 0,
        "LinkLag": lag * (1440 if elapsed else MINUTES_PER_DAY) * 10, "LagFormat": 8 if elapsed else 7,
    })


def _baseline(item, kind, span, wanted, calendar):
    """Базовый план 0: переданный параметром или плановые сроки задачи без сдвига."""
    if span is None:
        return None
    if isinstance(wanted, dict):
        start = _day(wanted.get("start") or wanted.get("date")) or span[0]
        end = _day(wanted.get("end") or wanted.get("date")) or span[1]
    else:
        start, end = span[0], span[1]
    if kind == "milestone":
        return [_element("Baseline", BASELINE_ORDER, {
            "Number": 0, "Interim": 0, "Start": _dt(start, DAY_FINISH), "Finish": _dt(start, DAY_FINISH),
            "Duration": _duration(0), "DurationFormat": 7, "EstimatedDuration": 0})]
    minutes = max(1, calendar.working_days(start, max(start, end))) * MINUTES_PER_DAY
    return [_element("Baseline", BASELINE_ORDER, {
        "Number": 0, "Interim": 0, "Start": _dt(start, DAY_START), "Finish": _dt(max(start, end), DAY_FINISH),
        "Duration": _duration(minutes), "DurationFormat": 7, "EstimatedDuration": 0})]
