"""Модель файла MS Project — неизменяемые записи (ADR-0012, раздел 2).

Одна модель и для MS Project XML (MSPDI), и для того, что в 0.7.x даст конвертер `.mpp` (MPXJ → MSPDI).
Значения хранятся в единицах файла, без пересчёта в «Пульс»: длительности и трудозатраты — в минутах рабочего
времени (как PT40H0M0S), лаг связи — в десятых долях минуты (как LinkLag), даты — datetime без часового пояса
(MS Project пишет местное время проекта). Пересчёт в дни и правила переноса — в mapping.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta


class MsProjectError(ValueError):
    """Файл нельзя прочитать как MS Project XML; текст — для сотрудника, по-русски."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# Тип связи PredecessorLink/Type
LINK_TYPES = {0: "FF", 1: "FS", 2: "SF", 3: "SS"}
LINK_TYPE_CODES = {value: key for key, value in LINK_TYPES.items()}

# ConstraintType → ограничение строки «Пульса» (раздел 5.4); 0 «Как можно раньше» — без ограничения
CONSTRAINT_TYPES = {0: None, 1: "alap", 2: "mso", 3: "mfo", 4: "snet", 5: "snlt", 6: "fnet", 7: "fnlt"}
CONSTRAINT_CODES = {value: key for key, value in CONSTRAINT_TYPES.items() if value}
# Ограничения на окончание: их дата — момент окончания (правило «окончание в 08:00 — предыдущий день»)
FINISH_CONSTRAINTS = {"mfo", "fnet", "fnlt"}

# Resource/Type
RESOURCE_MATERIAL, RESOURCE_WORK, RESOURCE_COST = 0, 1, 2

# Единицы длительности и лага (DurationFormat, LagFormat); +32 — та же единица с «?» (оценка)
FORMAT_MINUTES, FORMAT_ELAPSED_MINUTES = 3, 4
FORMAT_HOURS, FORMAT_ELAPSED_HOURS = 5, 6
FORMAT_DAYS, FORMAT_ELAPSED_DAYS = 7, 8
FORMAT_WEEKS, FORMAT_ELAPSED_WEEKS = 9, 10
FORMAT_MONTHS, FORMAT_ELAPSED_MONTHS = 11, 12
FORMAT_PERCENT, FORMAT_ELAPSED_PERCENT = 19, 20
FORMAT_NULL = 21
WORKING_FORMATS = {3, 5, 7, 9, 11}
ELAPSED_FORMATS = {4, 6, 8, 10, 12}
PERCENT_FORMATS = {19, 20}
ESTIMATED_SHIFT = 32

# Тип дня календаря (WeekDay/DayType): 1 — воскресенье … 7 — суббота; 0 — исключение в старом формате
DAY_TYPE_TO_ISO = {1: 7, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}

# Настраиваемые поля задач: FieldID от 188743680 (поля ресурсов — с 205520896, назначений — с 255852544)
TASK_FIELD_MIN, TASK_FIELD_MAX = 188743680, 205520895


def base_format(code):
    """Единица без признака оценки: 39 («5д?») → 7 («5д»)."""
    if code is None:
        return None
    return code - ESTIMATED_SHIFT if code >= 35 else code


def is_task_field(field_id):
    try:
        return TASK_FIELD_MIN <= int(field_id) <= TASK_FIELD_MAX
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class WorkingTime:
    start: time
    finish: time   # 00:00 у окончания — полночь (конец суток)


@dataclass(frozen=True, slots=True)
class WeekDay:
    day_type: int          # 1 — воскресенье … 7 — суббота
    working: bool
    times: tuple = ()      # (WorkingTime, …)


@dataclass(frozen=True, slots=True)
class CalendarException:
    """Исключение календаря: праздник, перенос, рабочий выходной. Повторение — поля Type, Period и др. как в файле."""

    name: str | None
    start: date
    finish: date
    working: bool
    times: tuple = ()
    type: int | None = None          # 1 — по дням (без повторения), 2–7 — повторение (см. dates())
    period: int | None = None
    days_of_week: int | None = None  # маска: 1 — воскресенье, 2 — понедельник … 64 — суббота
    month: int | None = None         # 0 — январь
    month_day: int | None = None
    month_item: int | None = None    # 3 — воскресенье … 9 — суббота
    month_position: int | None = None  # 0 — первый … 3 — четвёртый, 4 — последний
    occurrences: int | None = None

    def dates(self, limit=3660, low=None, high=None):
        """Даты исключения в пределах [low, high] (не больше limit) и признак, что повторение разобрано.

        Type 1 и 7 — каждый Period-й день отрезка, 6 — дни недели по маске раз в Period недель, 2 и 4 — число
        месяца ежегодно / раз в Period месяцев, 3 и 5 — «N-й (последний) день недели» месяца. Отрезок
        сначала обрезается окном [low, high] (шкала плана ± год): исключение «1900–9000» не перебирается день
        за днём, а «по 31.12.9999» не выходит за пределы дат.
        """
        kind = self.type or 1
        period = max(1, min(self.period or 1, 10000))
        first = max(self.start, low) if low else self.start
        last = min(self.finish, high) if high else self.finish
        if last < first or limit <= 0:
            return [], kind in (1, 2, 3, 4, 5, 6, 7)
        out = []
        if kind in (1, 7):
            # шаг Period от начала исключения, первый день — не раньше окна
            day = self.start + timedelta(days=-(-(first - self.start).days // period) * period)
            while day <= last and len(out) < limit:
                out.append(day)
                day += timedelta(days=period)
            return out, True
        if kind == 6:
            mask = (self.days_of_week or 0) & 0x7F
            if not mask:
                return [], True
            week0 = self.start - timedelta(days=self.start.isoweekday() % 7)  # воскресенье недели начала
            week = max(0, (first - week0).days // 7)
            week += -week % period
            while len(out) < limit:
                sunday = week0 + timedelta(weeks=week)
                if sunday > last:
                    break
                for offset in range(7):
                    day = sunday + timedelta(days=offset)
                    if first <= day <= last and mask & (1 << offset) and len(out) < limit:
                        out.append(day)
                week += period
            return out, True
        if kind in (2, 3, 4, 5):
            yearly = kind in (2, 3)
            step = 12 if yearly else period
            month0 = self.start.year * 12 + ((self.month % 12) if yearly and self.month is not None
                                               else self.start.month - 1)
            index = max(0, (first.year * 12 + first.month - 1 - month0) // step - 1)
            guard = 0
            while guard < 2400 and len(out) < limit:
                guard += 1
                year, month = divmod(month0 + index * step, 12)
                index += 1
                if date(year, month + 1, 1) > last:
                    break
                day = self._month_day(year, month + 1, kind)
                if day is not None and first <= day <= last and day >= self.start:
                    out.append(day)
            return out, True
        return [], False

    def _month_day(self, year, month, kind):
        if kind in (2, 4):
            day = self.month_day or 1
            try:
                return date(year, month, day)
            except ValueError:
                return None
        weekday = ((self.month_item or 3) - 3) % 7          # 0 — воскресенье
        position = self.month_position or 0
        first = date(year, month, 1)
        days = [first + timedelta(days=i) for i in range(31) if (first + timedelta(days=i)).month == month]
        matching = [d for d in days if d.isoweekday() % 7 == weekday]
        if not matching:
            return None
        return matching[-1] if position >= 4 or position >= len(matching) else matching[position]


@dataclass(frozen=True, slots=True)
class WorkWeek:
    """Нестандартная рабочая неделя календаря на отрезке дат (MS Project 2007+)."""

    name: str | None
    start: date | None
    finish: date | None
    week_days: tuple = ()


@dataclass(frozen=True, slots=True)
class Calendar:
    uid: int
    name: str | None
    is_base: bool
    base_uid: int | None
    week_days: dict = field(default_factory=dict)   # DayType → WeekDay
    exceptions: tuple = ()
    work_weeks: tuple = ()

    def day_start(self):
        """Начало рабочего дня — самое раннее время начала по рабочим дням недели; None, если не задано."""
        starts = [t.start for day in self.week_days.values() if day.working for t in day.times]
        return min(starts) if starts else None


@dataclass(frozen=True, slots=True)
class ExtendedAttributeDef:
    field_id: str
    field_name: str | None
    alias: str | None

    @property
    def label(self):
        return self.alias or self.field_name or self.field_id


@dataclass(frozen=True, slots=True)
class Baseline:
    number: int
    start: datetime | None
    finish: datetime | None
    duration: float | None   # минуты


@dataclass(frozen=True, slots=True)
class Link:
    predecessor_uid: int
    successor_uid: int
    type: int = 1                 # 0 FF, 1 FS, 2 SF, 3 SS
    lag_tenths: int = 0           # десятые доли минуты; у лага в процентах — сами проценты
    lag_format: int = FORMAT_DAYS
    cross_project: bool = False
    cross_project_name: str | None = None

    @property
    def type_code(self):
        return LINK_TYPES.get(self.type, "FS")


@dataclass(frozen=True, slots=True)
class Task:
    uid: int
    guid: str | None = None
    id: int | None = None
    name: str = ""
    outline_level: int | None = None
    outline_number: str | None = None
    wbs: str | None = None
    summary: bool = False
    milestone: bool = False
    active: bool = True
    is_null: bool = False
    manual: bool = False
    recurring: bool = False
    start: datetime | None = None
    finish: datetime | None = None
    duration: float | None = None          # минуты
    duration_format: int | None = None
    percent_complete: int = 0
    actual_start: datetime | None = None
    actual_finish: datetime | None = None
    constraint_type: int = 0
    constraint_date: datetime | None = None
    deadline: datetime | None = None
    notes: str | None = None
    hyperlink: str | None = None
    hyperlink_address: str | None = None
    priority: int | None = None
    baselines: dict = field(default_factory=dict)     # номер → Baseline
    extended: dict = field(default_factory=dict)      # FieldID → значение
    critical: bool = False
    total_slack: float | None = None       # минуты
    external: bool = False
    external_project: str | None = None
    is_subproject: bool = False
    calendar_uid: int | None = None
    cost: float | None = None
    fixed_cost: float | None = None
    manual_start: datetime | None = None
    manual_finish: datetime | None = None

    @property
    def constraint(self):
        return CONSTRAINT_TYPES.get(self.constraint_type)


@dataclass(frozen=True, slots=True)
class Resource:
    uid: int
    name: str = ""
    type: int = RESOURCE_WORK
    guid: str | None = None
    id: int | None = None
    initials: str | None = None
    group: str | None = None
    email: str | None = None
    is_null: bool = False
    is_inactive: bool = False


@dataclass(frozen=True, slots=True)
class Assignment:
    task_uid: int
    resource_uid: int
    uid: int | None = None
    units: float = 1.0
    work: float = 0.0          # минуты


@dataclass(frozen=True, slots=True)
class Project:
    name: str | None = None
    title: str | None = None
    guid: str | None = None
    start: datetime | None = None
    finish: datetime | None = None
    minutes_per_day: int = 480
    minutes_per_week: int = 2400
    days_per_month: int = 20
    calendar_uid: int | None = None
    default_start_time: time | None = None
    default_finish_time: time | None = None
    schedule_from_start: bool = True
    save_version: str | None = None
    extended_attributes: dict = field(default_factory=dict)   # FieldID → ExtendedAttributeDef
    calendars: tuple = ()
    tasks: tuple = ()
    links: tuple = ()
    resources: tuple = ()
    assignments: tuple = ()

    @property
    def calendar(self):
        """Календарь проекта (CalendarUID), иначе первый базовый."""
        by_uid = {c.uid: c for c in self.calendars}
        if self.calendar_uid in by_uid:
            return by_uid[self.calendar_uid]
        return next((c for c in self.calendars if c.is_base), self.calendars[0] if self.calendars else None)

    def week_days(self, calendar):
        """Рабочая неделя календаря с учётом базового: дни, которых нет в производном, берутся из базового."""
        by_uid = {c.uid: c for c in self.calendars}
        days, seen, node = {}, set(), calendar
        while node is not None and node.uid not in seen:
            seen.add(node.uid)
            for day_type, day in node.week_days.items():
                days.setdefault(day_type, day)
            node = by_uid.get(node.base_uid)
        return days
