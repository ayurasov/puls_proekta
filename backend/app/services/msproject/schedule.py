"""Связи плана, согласованные с расчётом сроков «Пульса» (ADR-0012, раздел 3: «даты в файле — главные»).

MS Project считает связи в часах рабочего времени, «Пульс» — в днях (src/gantt/domain/dependencies.js): лаг «4ч»,
связь SF, прошедший лаг, приходящийся на выходной, задача, поставленная позже, чем требуют связи (выравнивание,
ограничения, ручные даты), — и автосдвиг «Пульса» сразу передвинул бы строки, которые в файле стоят иначе.
Поэтому после перевода лагов в дни `align_links` подбирает лаги так, чтобы расчёт «Пульса» дал ровно даты
файла: у каждой последующей строки определяющая связь (наибольшее ограничение) равна её дате, остальные её не
превышают. Лаг, который пришлось изменить, попадает в предупреждение «Лаги связей подобраны…».

Правила расчёта — те же, что в dependencies.js (computeDependencyShifts), и проверяются на обеих сторонах:
`inconsistent_rows` здесь и тест vitest msprojectConsistency на одних и тех же документах.

* Сроки предшественника: начало; окончание — со сдвигом (offset), у завершённой задачи — дата завершения;
  у вехи и то и другое — её дата.
* Последующая задача: FS — следующий день после окончания (+ лаг; при лаге в рабочих днях — следующий рабочий
  день + лаг), SS — начало (+ лаг), FF — окончание (+ лаг), SF — начало (+ лаг); FS/SS ограничивают начало,
  FF/SF — окончание, длительность (календарные дни) сохраняется; «Начало не ранее» — начало не раньше даты.
* Последующая веха: FS и FF — окончание предшественника (+ лаг), SS и SF — начало (+ лаг).
* Лаг в рабочих днях отсчитывается по производственному календарю «Пульса» (createCalendar в calendar.js).
"""

from __future__ import annotations

from datetime import date, timedelta

MAX_LAG = 3650          # как plan_schema.MAX_LAG: больше сервер не примет
MAX_STEPS = 3660        # дальше рабочие дни не считаются — лаг становится календарным


class RuCalendar:
    """Рабочие дни производственного календаря «Пульса» (как createCalendar в src/gantt/domain/calendar.js)."""

    def __init__(self, holidays=None, workdays=None):
        if holidays is None and workdays is None:
            from app.calendar_data import OFFICIAL_DAYS

            holidays = [d for year in OFFICIAL_DAYS.values() for d in year["holiday"]]
            workdays = [d for year in OFFICIAL_DAYS.values() for d in year["workday"]]
        self.holidays = {str(d)[:10] for d in holidays or ()}
        self.workdays = {str(d)[:10] for d in workdays or ()}

    def is_working(self, day):
        text = day.isoformat()
        if text in self.holidays:
            return False
        if text in self.workdays:
            return True
        return day.isoweekday() < 6

    def add_working_days(self, start, count):
        """Окончание задачи длиной count рабочих дней от start (start входит в срок, если рабочий)."""
        remaining = max(1, count)
        day = start
        if self.is_working(day):
            remaining -= 1
        guard = 0
        while remaining > 0 and guard < 20000:
            day += timedelta(days=1)
            if self.is_working(day):
                remaining -= 1
            guard += 1
        return day

    def shift_working_days(self, day, n):
        """Сдвиг на n рабочих дней вперёд или назад (shiftWorkingDays в dependencies.js)."""
        left, step, guard = abs(int(n)), (-1 if n < 0 else 1), 0
        while left > 0 and guard < 20000:
            day += timedelta(days=step)
            if self.is_working(day):
                left -= 1
            guard += 1
        return day

    def working_steps(self, base, target):
        """n, при котором shift_working_days(base, n) == target; None — если target не рабочий день или далеко."""
        if target == base:
            return 0
        if not self.is_working(target) or abs((target - base).days) > MAX_STEPS:
            return None
        step = 1 if target > base else -1
        n, day = 0, base
        while day != target:
            day += timedelta(days=step)
            if self.is_working(day):
                n += 1
        return n * step

    def export(self):
        return {"holidays": sorted(self.holidays), "workdays": sorted(self.workdays)}


def _day(value):
    return date.fromisoformat(value) if value else None


def predecessor_dates(item):
    """(начало, окончание) предшественника для связей (predecessorDates в dependencies.js)."""
    if item.get("type") == "milestone":
        day = _day(item.get("date"))
        return day, day
    start = _day(item.get("start"))
    if item.get("status") == "done" and item.get("completedDate"):
        return start, _day(item["completedDate"])
    end = _day(item.get("end"))
    return start, (end + timedelta(days=int(item.get("offset") or 0)) if end else None)


def _base(link, predecessor):
    start, end = predecessor_dates(predecessor)
    return end if link.get("type", "FS") in ("FS", "FF") else start


def _extra_day(link, successor):
    """FS к задаче — «следующий день» после окончания предшественника; к вехе — тот же день."""
    return 1 if link.get("type", "FS") == "FS" and successor.get("type") == "task" else 0


def link_value(link, predecessor, successor, calendar):
    """Дата, которую связь требует от последующей строки: начало (FS/SS) или окончание (FF/SF) задачи, дата вехи."""
    base = _base(link, predecessor)
    if base is None:
        return None
    lag = int(link.get("lag") or 0) + _extra_day(link, successor)
    if link.get("lagKind") == "working":
        return calendar.shift_working_days(base, lag)
    return base + timedelta(days=lag)


def _target(link, successor):
    if successor.get("type") == "milestone":
        return _day(successor.get("date"))
    return _day(successor.get("end") if link.get("type") in ("FF", "SF") else successor.get("start"))


def _snet(item):
    return _day(item.get("constraintDate")) if item.get("constraintType") == "snet" else None


def _linkable(item):
    return item is not None and item.get("type") in ("task", "milestone")


def _incoming(items, links):
    by_key = {item["key"]: item for item in items}
    incoming = {}
    for link in links:
        if _linkable(by_key.get(link.get("from"))) and _linkable(by_key.get(link.get("to"))) \
                and link["from"] != link["to"]:
            incoming.setdefault(link["to"], []).append(link)
    return by_key, incoming


def scheduled(item, links, by_key, calendar):
    """Сроки строки по расчёту «Пульса» при неподвижных предшественниках (shiftTask / shiftMilestone)."""
    snet = _snet(item)
    if item.get("type") == "milestone":
        day = _day(item.get("date"))
        values = [v for v in (link_value(x, by_key[x["from"]], item, calendar) for x in links) if v]
        best = max(values) if values else None
        if best is None and not (snet and day < snet):
            return {"date": item.get("date")}
        best = max(best, snet) if snet and best else (best or snet)
        return {"date": best.isoformat()}
    start, end = _day(item.get("start")), _day(item.get("end"))
    if start is None or end is None:
        return {"start": item.get("start"), "end": item.get("end")}
    start_bound = end_bound = None
    for link in links:
        value = link_value(link, by_key[link["from"]], item, calendar)
        if value is None:
            continue
        if link.get("type", "FS") in ("FS", "SS"):
            start_bound = value if start_bound is None else max(start_bound, value)
        else:
            end_bound = value if end_bound is None else max(end_bound, value)
    if start_bound is None and end_bound is None and not (snet and start < snet):
        return {"start": item["start"], "end": item["end"]}
    span = end - start
    mode = "start" if end_bound is None else "end" if start_bound is None else "both"
    new_start, new_end = start, end
    if mode == "start" and start_bound is not None:
        new_start = start_bound
    if mode == "end":
        new_end = end_bound
        new_start = end_bound - span
    if mode == "both":
        new_start = max(start_bound, end_bound - span)
    if snet and new_start < snet:
        new_start = snet
        if mode == "end":
            mode = "start"
    if mode != "end":
        new_end = new_start + span
    return {"start": new_start.isoformat(), "end": new_end.isoformat()}


def inconsistent_rows(items, links, calendar):
    """Ключи строк, которые автосдвиг «Пульса» передвинул бы (computeDependencyShifts вернул бы их)."""
    by_key, incoming = _incoming(items, links)
    out = []
    for item in items:
        if item.get("type") not in ("task", "milestone"):
            continue
        result = scheduled(item, incoming.get(item["key"], []), by_key, calendar)
        current = {"date": item.get("date")} if item["type"] == "milestone" else {
            "start": item.get("start"), "end": item.get("end")}
        if result != current:
            out.append(item["key"])
    return out


def _set_lag(link, base, extra, target, calendar):
    """Лаг, при котором связь требует ровно target. Рабочие дни сохраняются, если target — рабочий день по
    календарю «Пульса»; иначе лаг становится календарным."""
    if link.get("lagKind") == "working":
        steps = calendar.working_steps(base, target)
        if steps is not None and abs(steps - extra) <= MAX_LAG:
            link["lag"] = steps - extra
            return
    link["lagKind"] = "calendar"
    link["lag"] = max(-MAX_LAG, min(MAX_LAG, (target - base).days - extra))


def clamp_snet(items):
    """«Начало не ранее» позже начала строки в файле (MS Project не соблюдает ограничения, если связи важнее) —
    дата ограничения прижимается к началу: иначе «Пульс» сдвинул бы строку. Возвращает изменённые строки."""
    changed = []
    for item in items:
        snet = _snet(item)
        own = _day(item.get("date") if item.get("type") == "milestone" else item.get("start"))
        if snet and own and snet > own:
            item["constraintDate"] = own.isoformat()
            changed.append(item)
    return changed


def align_links(items, links, calendar):
    """Подбор лагов: расчёт «Пульса» по связям даёт ровно даты строк. Связи меняются на месте; возвращаются
    изменённые связи (с прежними лагом и видом лага в "before")."""
    by_key, incoming = _incoming(items, links)
    before = {id(link): (link.get("lag"), link.get("lagKind")) for link in links}
    for item in items:
        own = incoming.get(item["key"])
        if not own or item.get("type") not in ("task", "milestone"):
            continue
        if item["type"] == "task" and (not item.get("start") or not item.get("end")):
            continue
        # 1) связь, которая требует больше, чем стоит в файле, — ровно до даты файла
        for link in own:
            predecessor = by_key[link["from"]]
            value, target = link_value(link, predecessor, item, calendar), _target(link, item)
            if value is not None and target is not None and value > target:
                _set_lag(link, _base(link, predecessor), _extra_day(link, item), target, calendar)
        # 2) если ни одна связь (и ни «Начало не ранее») не держит строку на её дате — ближайшая становится
        #    определяющей: иначе автосдвиг подтянул бы строку раньше
        if scheduled(item, own, by_key, calendar) == _current(item):
            continue
        span = (_day(item["end"]) - _day(item["start"])) if item["type"] == "task" else timedelta(0)
        best, best_value = None, None
        for link in own:
            value = link_value(link, by_key[link["from"]], item, calendar)
            if value is None:
                continue
            effective = value - span if item["type"] == "task" and link.get("type") in ("FF", "SF") else value
            if best_value is None or effective > best_value:
                best, best_value = link, effective
        if best is not None:
            predecessor = by_key[best["from"]]
            _set_lag(best, _base(best, predecessor), _extra_day(best, item), _target(best, item), calendar)
    return [(link, before[id(link)]) for link in links if (link.get("lag"), link.get("lagKind")) != before[id(link)]]


def _current(item):
    if item["type"] == "milestone":
        return {"date": item.get("date")}
    return {"start": item.get("start"), "end": item.get("end")}
