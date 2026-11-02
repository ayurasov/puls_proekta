"""Чтение MS Project XML (MSPDI) в модель (ADR-0012, раздел 2).

Файл разбирается потоково (`XMLPullParser`): каждая задача, ресурс, назначение и календарь переводятся в
запись модели сразу после закрывающего тега и удаляются из дерева — файл в 50 МБ не превращается в дерево
в сотни мегабайт. Защита — та же, что у `defusedxml`, но без зависимости:

* объявление DOCTYPE (а с ним и сущности, внешние ссылки, «миллиард смехов») отклоняется до разбора —
  в MSPDI его не бывает; внешние сущности expat и сам не загружает;
* ограничения: файл до 50 МБ, до 20 000 задач, 50 000 ресурсов, 200 000 назначений, вложенность до 32.

Файл .mpp (двоичный OLE) и другие форматы узнаются по первым байтам — сотрудник получает подсказку, как
сохранить файл в XML. Ошибки — `MsProjectError` с текстом для сотрудника.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, time

from app.services.msproject.model import (
    FORMAT_DAYS,
    Assignment,
    Baseline,
    Calendar,
    CalendarException,
    ExtendedAttributeDef,
    Link,
    MsProjectError,
    Project,
    Resource,
    Task,
    WeekDay,
    WorkingTime,
    WorkWeek,
)

NAMESPACE = "http://schemas.microsoft.com/project"
MAX_BYTES = 50 * 1024 * 1024
MAX_TASKS = 20_000
MAX_RESOURCES = 50_000
MAX_ASSIGNMENTS = 200_000
MAX_CALENDARS = 5_000
MAX_DEPTH = 32
CHUNK = 1 << 20

SAVE_AS_XML = "в MS Project выберите «Файл → Сохранить как → XML (*.xml)»"
NOT_MSPDI = f"Это не файл MS Project XML — {SAVE_AS_XML}"
OLE_MAGIC = b"\xd0\xcf\x11\xe0"
# Кодировки, совместимые с ASCII: MS Project пишет UTF-8, сторонние программы — иногда windows-1251
ASCII_ENCODINGS = {"utf-8", "utf8", "us-ascii", "ascii", "windows-1251", "cp1251", "koi8-r", "iso-8859-5",
                   "iso-8859-1", "latin-1", "latin1", "windows-1252", "cp1252"}
_DECLARED_RE = re.compile(rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]{1,40})[\"']")
_DURATION_RE = re.compile(
    r"^(-)?P(?:(\d+(?:\.\d+)?)Y)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


# ---------------------------------------------------------------- значения
def parse_datetime(value):
    """'2026-03-02T08:00:00' → datetime (без часового пояса); пустое и неверное — None."""
    text = (value or "").strip()
    if not text:
        return None
    text = text.rstrip("Z")
    text = re.sub(r"[+-]\d{2}:\d{2}$", "", text) if "T" in text else text
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(text[:10]), time())
        except ValueError:
            return None


def parse_date(value):
    moment = parse_datetime(value)
    return moment.date() if moment else None


def parse_time(value):
    text = (value or "").strip()
    if not text:
        return None
    try:
        return time.fromisoformat(text[:8])
    except ValueError:
        return None


def parse_duration(value):
    """ISO 8601 'PT40H0M0S' (в том числе '-PT8H', 'P1DT2H') → минуты; неверное — None.

    Дни, недели, месяцы и годы в MSPDI не встречаются (MS Project пишет всё в часах); если встретятся — сутки
    считаются как 24 часа, месяц — 30 суток, год — 365 суток."""
    text = (value or "").strip().upper()
    if not text:
        return None
    match = _DURATION_RE.match(text)
    if not match or text in ("P", "PT", "-P", "-PT"):
        return None
    sign, years, months, weeks, days, hours, minutes, seconds = match.groups()
    total = (float(years or 0) * 365 * 1440 + float(months or 0) * 30 * 1440 + float(weeks or 0) * 7 * 1440
             + float(days or 0) * 1440 + float(hours or 0) * 60 + float(minutes or 0) + float(seconds or 0) / 60)
    return -total if sign else total


def _int(value, default=None):
    text = (value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        try:
            return int(round(float(text)))
        except (ValueError, OverflowError):
            return default


def _float(value, default=None):
    text = (value or "").strip()
    if not text:
        return default
    try:
        number = float(text)
    except ValueError:
        return default
    return number if number == number and abs(number) != float("inf") else default


def _tenths(value):
    """Десятые доли минуты (TotalSlack, LinkLag) → минуты."""
    number = _float(value)
    return number / 10 if number is not None else None


def _bool(value, default=False):
    text = (value or "").strip().lower()
    if not text:
        return default
    return text in ("1", "true", "-1", "yes")


def _text(value):
    text = (value or "").strip()
    return text or None


def _guid(value):
    text = (value or "").strip().strip("{}").upper()
    return text or None


# ---------------------------------------------------------------- проверка до разбора
def _read_bytes(source, max_bytes):
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    elif isinstance(source, str):
        data = source.encode("utf-8")
    elif hasattr(source, "read"):
        data = source.read(max_bytes + 1)
        if isinstance(data, str):
            data = data.encode("utf-8")
    else:
        raise TypeError("source: bytes, str или файл")
    if len(data) > max_bytes:
        size = f"{max_bytes // (1024 * 1024)} МБ" if max_bytes >= 1024 * 1024 else f"{max_bytes} байт"
        raise MsProjectError(f"Файл больше {size} — такой план не загрузить. "
                             "Разделите проект в MS Project на несколько файлов")
    if not data.strip():
        raise MsProjectError(f"Файл пустой — {SAVE_AS_XML}")
    return data


def _check_signature(data):
    head = data[:8]
    if head.startswith(OLE_MAGIC):
        raise MsProjectError("Файл .mpp: сохраните его в MS Project как XML — «Файл → Сохранить как → "
                             "XML (*.xml)» — и загрузите получившийся файл")
    if head.startswith(b"PK\x03\x04"):
        raise MsProjectError(f"Это архив или документ Office, а не MS Project XML — {SAVE_AS_XML}")
    if head.startswith(b"%PDF"):
        raise MsProjectError(f"Это PDF, а не MS Project XML — {SAVE_AS_XML}")
    if data.lstrip(b"\xef\xbb\xbf \t\r\n")[:4].upper() == b"MPX,":
        raise MsProjectError("Файл .mpx (MS Project 98) не загружается: откройте его в MS Project и выберите "
                             "«Файл → Сохранить как → XML (*.xml)»")


def _decode(data):
    """Текст файла для разбора. UTF-16 (с меткой порядка байтов или без неё) переводится в str — expat разбирает
    его как UTF-8, не глядя на объявление. Остальное остаётся байтами в кодировке, совместимой с ASCII: только в
    такой DOCTYPE в прологе виден побайтовой проверке, поэтому другие объявленные кодировки отклоняются."""
    if data.startswith((b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")):
        raise MsProjectError(f"Кодировка UTF-32 не поддерживается — {SAVE_AS_XML}")
    codec = None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        codec = "utf-16"
    elif data.startswith(b"<\x00"):
        codec = "utf-16-le"
    elif data.startswith(b"\x00<"):
        codec = "utf-16-be"
    if codec:
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            raise MsProjectError(f"Файл повреждён: неверная кодировка UTF-16 — {SAVE_AS_XML}") from None
    declared = _DECLARED_RE.match(data[3:] if data.startswith(b"\xef\xbb\xbf") else data)
    if declared:
        encoding = declared.group(1).decode("ascii").lower()
        if encoding not in ASCII_ENCODINGS and not encoding.startswith("utf-16") and encoding != "utf16":
            raise MsProjectError(f"Кодировка файла «{encoding}» не поддерживается — {SAVE_AS_XML}")
    return data


def _prolog_end(text):
    """Пролог XML (до корневого элемента) проверяется на DOCTYPE и сущности: в MSPDI их не бывает, а через них
    идут атаки «миллиард смехов» и чтение файлов сервера. После корня объявление DOCTYPE expat сам отклоняет
    как ошибку разбора, а без DOCTYPE сущностей объявить нельзя. Возвращает позицию корневого элемента."""
    is_bytes = isinstance(text, bytes)
    lt, bang, question = (b"<", b"<!", b"<?") if is_bytes else ("<", "<!", "<?")
    pos, size = 0, len(text)
    while pos < size:
        start = text.find(lt, pos)
        if start < 0:
            return size
        if text.startswith(bang + (b"--" if is_bytes else "--"), start):
            end = text.find(b"-->" if is_bytes else "-->", start + 4)
            if end < 0:
                return size
            pos = end + 3
            continue
        if text.startswith(bang, start):
            raise MsProjectError("Файл содержит объявление DOCTYPE или сущностей — MS Project таких файлов не "
                                 "создаёт, загрузка отклонена. Сохраните план из MS Project заново: «Файл → "
                                 "Сохранить как → XML (*.xml)»")
        if text.startswith(question, start):
            end = text.find(b"?>" if is_bytes else "?>", start + 2)
            if end < 0:
                return size
            pos = end + 2
            continue
        return start
    return size


# ---------------------------------------------------------------- разбор элементов
_LOCAL = {}


def _local(tag):
    name = _LOCAL.get(tag)
    if name is None:
        name = tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""
        _LOCAL[tag] = name
    return name


def _fields(elem):
    """Простые дочерние элементы: имя → текст (первое вхождение)."""
    out = {}
    for child in elem:
        name = _local(child.tag)
        if name not in out:
            out[name] = child.text
    return out


def _working_times(elem):
    times = []
    for child in elem:
        if _local(child.tag) != "WorkingTimes":
            continue
        for wt in child:
            f = _fields(wt)
            start, finish = parse_time(f.get("FromTime")), parse_time(f.get("ToTime"))
            if start is not None and finish is not None:
                times.append(WorkingTime(start, finish))
    return tuple(times)


def _time_period(elem):
    for child in elem:
        if _local(child.tag) == "TimePeriod":
            f = _fields(child)
            return parse_date(f.get("FromDate")), parse_date(f.get("ToDate"))
    return None, None


def _week_day(elem):
    f = _fields(elem)
    times = _working_times(elem)
    working = _bool(f.get("DayWorking"), bool(times))
    return WeekDay(_int(f.get("DayType"), 0), working, times if working else ())


def _calendar(elem):
    f = _fields(elem)
    week_days, legacy, exceptions, work_weeks = {}, [], [], []
    has_exceptions = False
    for child in elem:
        name = _local(child.tag)
        if name == "WeekDays":
            for wd in child:
                day = _week_day(wd)
                if day.day_type == 0:
                    start, finish = _time_period(wd)
                    if start and finish:
                        legacy.append(CalendarException(None, start, finish, day.working, day.times))
                elif 1 <= day.day_type <= 7:
                    week_days[day.day_type] = day
        elif name == "Exceptions":
            has_exceptions = True
            for ex in child:
                ef = _fields(ex)
                start, finish = _time_period(ex)
                if not start or not finish:
                    continue
                times = _working_times(ex)
                exceptions.append(CalendarException(
                    _text(ef.get("Name")), start, finish, _bool(ef.get("DayWorking"), bool(times)), times,
                    type=_int(ef.get("Type")), period=_int(ef.get("Period")),
                    days_of_week=_int(ef.get("DaysOfWeek")), month=_int(ef.get("Month")),
                    month_day=_int(ef.get("MonthDay")), month_item=_int(ef.get("MonthItem")),
                    month_position=_int(ef.get("MonthPosition")), occurrences=_int(ef.get("Occurrences")),
                ))
        elif name == "WorkWeeks":
            for ww in child:
                wf = _fields(ww)
                start, finish = _time_period(ww)
                days = []
                for part in ww:
                    if _local(part.tag) == "WeekDays":
                        days.extend(_week_day(wd) for wd in part)
                work_weeks.append(WorkWeek(_text(wf.get("Name")), start, finish, tuple(days)))
    # MS Project 2007+ пишет исключения дважды: в Exceptions и (для старых версий) днями типа 0 в WeekDays
    return Calendar(
        uid=_int(f.get("UID"), 0), name=_text(f.get("Name")), is_base=_bool(f.get("IsBaseCalendar")),
        base_uid=_int(f.get("BaseCalendarUID")), week_days=week_days,
        exceptions=tuple(exceptions if has_exceptions else legacy), work_weeks=tuple(work_weeks),
    )


def _task(elem):
    f, links, baselines, extended = {}, [], {}, {}
    for child in elem:
        name = _local(child.tag)
        if name == "PredecessorLink":
            lf = _fields(child)
            pred = _int(lf.get("PredecessorUID"))
            if pred is not None:
                links.append((pred, _int(lf.get("Type"), 1), _int(lf.get("LinkLag"), 0),
                              _int(lf.get("LagFormat"), FORMAT_DAYS), _bool(lf.get("CrossProject")),
                              _text(lf.get("CrossProjectName"))))
        elif name == "Baseline":
            bf = _fields(child)
            number = _int(bf.get("Number"), 0)
            baselines[number] = Baseline(number, parse_datetime(bf.get("Start")), parse_datetime(bf.get("Finish")),
                                         parse_duration(bf.get("Duration")))
        elif name == "ExtendedAttribute":
            ef = _fields(child)
            field_id = _text(ef.get("FieldID"))
            if field_id:
                extended[field_id] = (ef.get("Value") or "").strip()
        elif name not in f:
            f[name] = child.text
    uid = _int(f.get("UID"))
    if uid is None:
        return None, []
    task = Task(
        uid=uid, guid=_guid(f.get("GUID")), id=_int(f.get("ID")), name=(f.get("Name") or "").strip(),
        outline_level=_int(f.get("OutlineLevel")), outline_number=_text(f.get("OutlineNumber")),
        wbs=_text(f.get("WBS")), summary=_bool(f.get("Summary")), milestone=_bool(f.get("Milestone")),
        active=_bool(f.get("Active"), True), is_null=_bool(f.get("IsNull")), manual=_bool(f.get("Manual")),
        recurring=_bool(f.get("Recurring")), start=parse_datetime(f.get("Start")),
        finish=parse_datetime(f.get("Finish")), duration=parse_duration(f.get("Duration")),
        duration_format=_int(f.get("DurationFormat")),
        percent_complete=max(0, min(100, _int(f.get("PercentComplete"), 0))),
        actual_start=parse_datetime(f.get("ActualStart")), actual_finish=parse_datetime(f.get("ActualFinish")),
        constraint_type=_int(f.get("ConstraintType"), 0), constraint_date=parse_datetime(f.get("ConstraintDate")),
        deadline=parse_datetime(f.get("Deadline")), notes=f.get("Notes") or None,
        hyperlink=_text(f.get("Hyperlink")), hyperlink_address=_text(f.get("HyperlinkAddress")),
        priority=_int(f.get("Priority")), baselines=baselines, extended=extended,
        critical=_bool(f.get("Critical")), total_slack=_tenths(f.get("TotalSlack")),
        external=_bool(f.get("ExternalTask")), external_project=_text(f.get("ExternalTaskProject")),
        is_subproject=_bool(f.get("IsSubproject")), calendar_uid=_int(f.get("CalendarUID")),
        cost=_float(f.get("Cost")), fixed_cost=_float(f.get("FixedCost")),
        manual_start=parse_datetime(f.get("ManualStart")), manual_finish=parse_datetime(f.get("ManualFinish")),
    )
    return task, [Link(pred, uid, kind, lag, fmt, cross, cross_name)
                  for pred, kind, lag, fmt, cross, cross_name in links]


def _resource(elem):
    f = _fields(elem)
    uid = _int(f.get("UID"))
    if uid is None:
        return None
    return Resource(
        uid=uid, name=" ".join((f.get("Name") or "").split()), type=_int(f.get("Type"), 1), guid=_guid(f.get("GUID")),
        id=_int(f.get("ID")), initials=_text(f.get("Initials")), group=_text(f.get("Group")),
        email=_text(f.get("EmailAddress")), is_null=_bool(f.get("IsNull")), is_inactive=_bool(f.get("IsInactive")),
    )


def _assignment(elem):
    f = _fields(elem)
    task_uid, resource_uid = _int(f.get("TaskUID")), _int(f.get("ResourceUID"))
    if task_uid is None or resource_uid is None:
        return None
    return Assignment(task_uid=task_uid, resource_uid=resource_uid, uid=_int(f.get("UID")),
                      units=_float(f.get("Units"), 1.0), work=parse_duration(f.get("Work")) or 0.0)


def _extended_def(elem):
    f = _fields(elem)
    field_id = _text(f.get("FieldID"))
    if not field_id:
        return None
    return ExtendedAttributeDef(field_id, _text(f.get("FieldName")), _text(f.get("Alias")))


# ---------------------------------------------------------------- чтение
def read_mspdi(source, *, max_bytes=MAX_BYTES, max_tasks=MAX_TASKS, max_resources=MAX_RESOURCES,
               max_assignments=MAX_ASSIGNMENTS, max_depth=MAX_DEPTH):
    """Файл MS Project XML (bytes, str или файловый объект) → `Project`.

    Ошибки формата, размера и безопасности — `MsProjectError` с текстом для сотрудника."""
    data = _read_bytes(source, max_bytes)
    _check_signature(data)
    text = _decode(data)
    _prolog_end(text)

    parser = ET.XMLPullParser(events=("start", "end"))
    stack, containers = [], {}
    root = None
    props, ext_defs = {}, {}
    calendars, tasks, links, resources, assignments = [], [], [], [], []

    def handle(event, elem):
        nonlocal root
        if event == "start":
            stack.append(_local(elem.tag))
            depth = len(stack)
            if depth > max_depth:
                raise MsProjectError(f"Файл повреждён: вложенность элементов больше {max_depth}")
            if depth == 1:
                namespace = elem.tag[1:].split("}", 1)[0] if elem.tag.startswith("{") else ""
                if stack[0] != "Project" or namespace not in (NAMESPACE, ""):
                    raise MsProjectError(NOT_MSPDI)
                root = elem
            elif depth == 2:
                containers[stack[1]] = elem
            return
        depth = len(stack)
        name = stack.pop()
        if depth == 2:
            if len(elem) == 0:
                props.setdefault(name, elem.text)
            root.remove(elem)
            return
        if depth != 3:
            return
        container = stack[-1]
        if container == "Tasks" and name == "Task":
            task, task_links = _task(elem)
            if task is not None:
                tasks.append(task)
                links.extend(task_links)
                if len(tasks) > max_tasks:
                    raise MsProjectError(f"В файле больше {max_tasks} задач — такой план не загрузить. "
                                         "Разделите проект в MS Project на несколько файлов")
        elif container == "Resources" and name == "Resource":
            resource = _resource(elem)
            if resource is not None:
                resources.append(resource)
                if len(resources) > max_resources:
                    raise MsProjectError(f"В файле больше {max_resources} ресурсов — такой план не загрузить")
        elif container == "Assignments" and name == "Assignment":
            assignment = _assignment(elem)
            if assignment is not None:
                assignments.append(assignment)
                if len(assignments) > max_assignments:
                    raise MsProjectError(f"В файле больше {max_assignments} назначений — такой план не загрузить")
        elif container == "Calendars" and name == "Calendar":
            calendars.append(_calendar(elem))
            if len(calendars) > MAX_CALENDARS:
                raise MsProjectError(f"В файле больше {MAX_CALENDARS} календарей — такой план не загрузить")
        elif container == "ExtendedAttributes" and name == "ExtendedAttribute":
            definition = _extended_def(elem)
            if definition is not None:
                ext_defs.setdefault(definition.field_id, definition)
        parent = containers.get(container)
        if parent is not None:
            parent.remove(elem)

    try:
        for offset in range(0, len(text), CHUNK):
            parser.feed(text[offset:offset + CHUNK])
            for event, elem in parser.read_events():
                handle(event, elem)
        parser.close()
        for event, elem in parser.read_events():
            handle(event, elem)
    except MsProjectError:
        raise
    except ET.ParseError as error:
        line = getattr(error, "position", (None, None))[0]
        if root is None:
            raise MsProjectError(NOT_MSPDI) from None
        where = f" (строка {line})" if line else ""
        raise MsProjectError(f"Файл MS Project XML повреждён{where}: сохраните его из MS Project заново") from None
    except (ValueError, LookupError):
        # Кодировка, которую expat не разбирает (многобайтная, кроме UTF-8/16)
        raise MsProjectError(f"Кодировка файла не поддерживается — {SAVE_AS_XML}") from None
    if root is None:
        raise MsProjectError(NOT_MSPDI)

    return Project(
        name=_text(props.get("Name")), title=_text(props.get("Title")), guid=_guid(props.get("GUID")),
        start=parse_datetime(props.get("StartDate")), finish=parse_datetime(props.get("FinishDate")),
        minutes_per_day=_positive(props.get("MinutesPerDay"), 480),
        minutes_per_week=_positive(props.get("MinutesPerWeek"), 2400),
        days_per_month=_positive(props.get("DaysPerMonth"), 20), calendar_uid=_int(props.get("CalendarUID")),
        default_start_time=parse_time(props.get("DefaultStartTime")),
        default_finish_time=parse_time(props.get("DefaultFinishTime")),
        schedule_from_start=_bool(props.get("ScheduleFromStart"), True), save_version=_text(props.get("SaveVersion")),
        extended_attributes=ext_defs, calendars=tuple(calendars), tasks=tuple(tasks), links=tuple(links),
        resources=tuple(resources), assignments=tuple(assignments),
    )


def _positive(value, default):
    number = _int(value)
    return number if number and number > 0 else default


def read_file(path, **limits):
    """Чтение с диска (скрипты, тесты)."""
    with open(path, "rb") as handle:
        return read_mspdi(handle, **limits)
