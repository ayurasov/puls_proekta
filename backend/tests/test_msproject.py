"""MS Project (ADR-0012): чтение MSPDI, сопоставление с планом, обновление из файла, экспорт и круговой обход.

Файлы-образцы — tests/fixtures/msproject: «Внедрение CRM» (как сохраняет MS Project 2016: фазы, вехи, связи всех
типов с лагами в разных единицах, базовый план, крайние сроки, ограничения, ресурсы и назначения, заметки в RTF и
HTML, гиперссылки, неактивная задача, пустая строка, повторяющаяся задача, внешняя задача и связь с другим
проектом), шестидневка с праздниками, .mpp и файлы с DOCTYPE для проверки отказа.
"""

import json
import random
import time
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.services.msproject import import_document
from app.services.msproject.diff import diff_plan, to_ops
from app.services.msproject.mapping import ITEM_LIMIT, MS_NOTE_PREFIX, convert_lag, map_project, round_half
from app.services.msproject.model import Link, MsProjectError
from app.services.msproject.reader import MAX_BYTES, parse_duration, read_file, read_mspdi
from app.services.msproject.schedule import RuCalendar, inconsistent_rows
from app.services.msproject.text import plain_text
from app.services.msproject.writer import write_mspdi

FIXTURES = Path(__file__).parent / "fixtures" / "msproject"
CRM = FIXTURES / "vnedrenie_crm.xml"
SIX_DAY = FIXTURES / "calendar_6day.xml"
NS = "http://schemas.microsoft.com/project"
Q = "{" + NS + "}"
JIRA = "https://jira.romashka.ru"
USERS = [
    {"id": 7, "name": "Иванов И. И.", "email": "IVANOV@romashka.ru"},
    {"id": 8, "name": "Семин  Петр Алексеевич", "email": ""},
    {"id": 9, "name": "Козлов Олег Игоревич", "email": "kozlov@romashka.ru"},
]
OPTIONS = {"categoryField": "188743731", "users": USERS, "jiraBaseUrls": [JIRA]}


@pytest.fixture(scope="module")
def crm():
    return read_file(CRM)


@pytest.fixture(scope="module")
def doc(crm):
    return map_project(crm, OPTIONS)


def row(document, uid):
    return next(i for i in document["items"] if i["extra"]["msp"]["uid"] == uid)


def link(document, key):
    return next((x for x in document["links"] if x["key"] == key), None)


def warning(document, code):
    return next((w for w in document["warnings"] if w["code"] == code), None)


def comment(document, key):
    return next((c["text"] for c in document["comments"] if c["itemKey"] == key), None)


def mspdi(tasks, *, resources="", assignments="", calendars="", head=""):
    """Небольшой файл MSPDI для проверки одного правила."""
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<Project xmlns="{NS}"><Name>Проба.xml</Name>{head}'
            f"<MinutesPerDay>480</MinutesPerDay><Calendars>{calendars}</Calendars><Tasks>{tasks}</Tasks>"
            f"<Resources>{resources}</Resources><Assignments>{assignments}</Assignments></Project>").encode()


def task(uid, name, start="2026-03-02T08:00:00", finish="2026-03-06T17:00:00", level=1, body="",
         duration="PT40H0M0S"):
    return (f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>{name}</Name><OutlineLevel>{level}</OutlineLevel>"
            f"<Start>{start}</Start><Finish>{finish}</Finish><Duration>{duration}</Duration>{body}</Task>")


def pred(uid, kind=1, lag=0, fmt=7):
    return (f"<PredecessorLink><PredecessorUID>{uid}</PredecessorUID><Type>{kind}</Type><LinkLag>{lag}</LinkLag>"
            f"<LagFormat>{fmt}</LagFormat></PredecessorLink>")


def resource(uid, name, kind=1, email=""):
    mail = f"<EmailAddress>{email}</EmailAddress>" if email else ""
    return f"<Resource><UID>{uid}</UID><ID>{uid}</ID><Name>{name}</Name><Type>{kind}</Type>{mail}</Resource>"


def assignment(task_uid, resource_uid, work="PT8H0M0S", units="1"):
    return (f"<Assignment><TaskUID>{task_uid}</TaskUID><ResourceUID>{resource_uid}</ResourceUID>"
            f"<Units>{units}</Units><Work>{work}</Work></Assignment>")


def mapped(data, **options):
    return map_project(read_mspdi(data), options)


# ================================================================ чтение
def test_чтение_свойств_проекта(crm):
    assert crm.title == "Внедрение CRM в ООО «Ромашка»"
    assert crm.name == "Внедрение CRM.xml"
    assert len(crm.guid) == 36 and crm.guid == crm.guid.upper()
    assert crm.start == datetime(2026, 3, 2, 8, 0) and crm.finish == datetime(2026, 7, 29, 17, 0)
    assert (crm.minutes_per_day, crm.minutes_per_week, crm.days_per_month) == (480, 2400, 20)
    assert crm.calendar_uid == 1 and crm.default_start_time.hour == 8 and crm.schedule_from_start
    assert crm.save_version == "14"
    text1 = crm.extended_attributes["188743731"]
    assert (text1.field_name, text1.alias, text1.label) == ("Text1", "Этап", "Этап")
    assert crm.extended_attributes["188743752"].alias == "Ключевая для заказчика"
    assert len(crm.tasks) == 45 and len(crm.resources) == 5 and len(crm.assignments) == 31


def test_чтение_полей_задачи(crm):
    by_uid = {t.uid: t for t in crm.tasks}
    t = by_uid[6]
    assert (t.id, t.name, t.outline_level) == (6, "Интервью с подразделениями", 2)
    assert (t.outline_number, t.wbs) == ("2.1", "2.1")
    assert t.start == datetime(2026, 3, 10, 8) and t.finish == datetime(2026, 3, 20, 17)
    assert t.duration == 72 * 60 and t.duration_format == 7
    assert t.percent_complete == 60 and t.actual_start == datetime(2026, 3, 10, 8) and t.actual_finish is None
    assert t.baselines[0].finish == datetime(2026, 3, 19, 17) and t.baselines[0].duration == 64 * 60
    assert t.baselines[1].start == datetime(2026, 3, 5, 8)
    assert t.extended == {"188743731": "Обследование"}
    assert t.critical and t.total_slack == 0 and t.active and not t.summary and not t.milestone
    assert by_uid[11].constraint_type == 4 and by_uid[11].constraint == "snet"
    assert by_uid[11].constraint_date == datetime(2026, 3, 30, 8)
    assert by_uid[11].hyperlink_address == "https://jira.romashka.ru/browse/CRM-101"
    assert by_uid[9].deadline == datetime(2026, 3, 27, 17)
    assert by_uid[20].manual and not by_uid[24].active and by_uid[25].is_null
    assert by_uid[34].recurring and by_uid[34].summary
    assert by_uid[39].external and by_uid[39].external_project.endswith("Инфраструктура.mpp")
    assert by_uid[30].priority == 800 and by_uid[30].calendar_uid == 2 and by_uid[30].fixed_cost == 50000
    assert by_uid[3].notes.startswith("{\\rtf1") and by_uid[0].outline_level == 0
    assert by_uid[13].milestone and by_uid[44].duration == 0 and not by_uid[44].milestone


def test_чтение_связей(crm):
    assert len(crm.links) == 33
    ss = next(x for x in crm.links if (x.predecessor_uid, x.successor_uid) == (6, 7))
    assert (ss.type, ss.type_code, ss.lag_tenths, ss.lag_format) == (3, "SS", 14400, 7)
    cross = next(x for x in crm.links if x.predecessor_uid == 999)
    assert cross.cross_project and cross.cross_project_name.endswith("Инфраструктура.mpp\\12")
    sf = next(x for x in crm.links if (x.predecessor_uid, x.successor_uid) == (20, 21))
    assert sf.type_code == "SF"


def test_чтение_ресурсов_и_назначений(crm):
    by_uid = {r.uid: r for r in crm.resources}
    assert by_uid[1].name == "Иванов Иван Иванович" and by_uid[1].email == "ivanov@romashka.ru"
    assert by_uid[1].group == "Руководитель проекта" and by_uid[1].initials == "ИИИ" and by_uid[1].type == 1
    assert by_uid[4].type == 0 and by_uid[0].name == ""
    half = next(a for a in crm.assignments if (a.task_uid, a.resource_uid) == (6, 2))
    assert half.units == 0.5 and half.work == 36 * 60


def test_чтение_календарей(crm):
    standard, night = crm.calendars[0], crm.calendars[1]
    assert standard.name == "Стандартный" and standard.is_base and crm.calendar is standard
    assert not standard.week_days[1].working and standard.week_days[2].working
    assert [(t.start.hour, t.finish.hour) for t in standard.week_days[2].times] == [(8, 12), (13, 17)]
    assert standard.day_start().hour == 8
    holidays = {e.start: e.name for e in standard.exceptions}
    assert holidays[date(2026, 3, 9)] == "Перенос выходного (с 8 марта)"
    new_year = next(e for e in standard.exceptions if e.name == "Новогодние каникулы")
    assert (new_year.start, new_year.finish, new_year.working) == (date(2027, 1, 1), date(2027, 1, 8), False)
    assert night.week_days[2].times[0].finish.hour == 0
    ivanov = next(c for c in crm.calendars if c.name == "Иванов Иван Иванович")
    assert ivanov.base_uid == 1 and not ivanov.is_base and ivanov.exceptions[0].name == "Отпуск"
    assert crm.week_days(ivanov)[2].working  # рабочая неделя — от базового календаря


def test_чтение_исключений_старого_формата():
    project = read_file(SIX_DAY)
    legacy = project.calendars[1]
    assert legacy.exceptions[0].start == date(2027, 5, 3) and not legacy.exceptions[0].working


def test_разбор_длительности():
    assert parse_duration("PT40H0M0S") == 2400
    assert parse_duration("PT0H30M0S") == 30
    assert parse_duration("-PT8H0M0S") == -480
    assert parse_duration("PT7.5H") == 450
    assert parse_duration("P1DT2H") == 1560
    assert parse_duration("") is None and parse_duration("40h") is None and parse_duration("PT") is None


def test_чтение_файла_utf16_и_файлового_объекта():
    text = CRM.read_text(encoding="utf-8").replace('encoding="UTF-8"', 'encoding="UTF-16"')
    project = read_mspdi(text.encode("utf-16"))
    assert project.title == "Внедрение CRM в ООО «Ромашка»" and len(project.tasks) == 45
    with open(CRM, "rb") as handle:
        assert len(read_mspdi(handle).tasks) == 45


def test_чтение_файла_windows_1251():
    data = mspdi(task(1, "Монтаж стоек")).decode().replace('encoding="UTF-8"', 'encoding="windows-1251"')
    assert read_mspdi(data.encode("cp1251")).tasks[0].name == "Монтаж стоек"


def test_неподдерживаемая_кодировка_отклоняется():
    data = mspdi(task(1, "Задача")).replace(b'encoding="UTF-8"', b'encoding="UTF-7"')
    with pytest.raises(MsProjectError, match="Кодировка файла «utf-7» не поддерживается"):
        read_mspdi(data)


def test_doctype_в_utf16_без_метки_отклоняется():
    data = (FIXTURES / "billion_laughs.xml").read_text(encoding="utf-8").replace("UTF-8", "UTF-16")
    with pytest.raises(MsProjectError, match="DOCTYPE"):
        read_mspdi(data.encode("utf-16-le"))
    with pytest.raises(MsProjectError, match="DOCTYPE"):
        read_mspdi(data.encode("utf-16-be"))


def test_чтение_без_пространства_имён():
    data = mspdi(task(1, "Задача")).replace(f' xmlns="{NS}"'.encode(), b"")
    assert read_mspdi(data).tasks[0].name == "Задача"


def test_doctype_в_заметке_cdata_не_мешает():
    data = mspdi(task(1, "Задача", body="<Notes><![CDATA[<!DOCTYPE html><p>текст</p>]]></Notes>"))
    assert read_mspdi(data).tasks[0].notes.startswith("<!DOCTYPE html>")


# ================================================================ отказ и ограничения
def test_mpp_отклоняется_с_подсказкой():
    with pytest.raises(MsProjectError, match="Файл .mpp: сохраните его в MS Project как XML"):
        read_file(FIXTURES / "plan.mpp")


@pytest.mark.parametrize("name", ["billion_laughs.xml", "external_entity.xml"])
def test_doctype_и_сущности_отклоняются(name):
    started = time.perf_counter()
    with pytest.raises(MsProjectError, match="DOCTYPE"):
        read_file(FIXTURES / name)
    assert time.perf_counter() - started < 1


def test_doctype_после_комментария_и_инструкции_отклоняется():
    data = b'<?xml version="1.0"?><!-- c --><?pi x?>\n<!DOCTYPE p [<!ENTITY a "b">]><Project/>'
    with pytest.raises(MsProjectError, match="DOCTYPE"):
        read_mspdi(data)


@pytest.mark.parametrize("data, text", [
    (b"", "Файл пустой"),
    (b"hello, world", "Это не файл MS Project XML"),
    (b"<html><body>nope</body></html>", "Это не файл MS Project XML"),
    (b'<Project xmlns="urn:other"><Name>x</Name></Project>', "Это не файл MS Project XML"),
    (b"PK\x03\x04rest-of-zip", "архив или документ Office"),
    (b"%PDF-1.7", "PDF"),
    (b"MPX,Microsoft Project for Windows,4.0,ANSI", "Файл .mpx"),
])
def test_не_ms_project_xml(data, text):
    with pytest.raises(MsProjectError, match=text) as error:
        read_mspdi(data)
    assert "Сохранить как → XML" in error.value.message


def test_повреждённый_xml():
    data = CRM.read_bytes()[:20000]
    with pytest.raises(MsProjectError, match="повреждён"):
        read_mspdi(data)


def test_ограничение_размера():
    with pytest.raises(MsProjectError, match="Файл больше 50 МБ"):
        read_mspdi(b"<" + b" " * MAX_BYTES)


def test_ограничения_числа_задач_назначений_и_вложенности():
    with pytest.raises(MsProjectError, match="больше 10 задач"):
        read_file(CRM, max_tasks=10)
    with pytest.raises(MsProjectError, match="больше 5 назначений"):
        read_file(CRM, max_assignments=5)
    deep = "<X>" * 40 + "</X>" * 40
    with pytest.raises(MsProjectError, match="вложенность"):
        read_mspdi(mspdi(task(1, "Задача", body=deep)))


def test_параметры_загрузки_проверяются(crm):
    with pytest.raises(MsProjectError, match="Длительности"):
        map_project(crm, {"durations": "hours"})
    with pytest.raises(MsProjectError, match="Сроки"):
        map_project(crm, {"baseline": "interim"})


# ================================================================ сопоставление: строки
def test_суммарная_задача_проекта_не_строка(doc):
    assert all(i["extra"]["msp"]["uid"] != 0 for i in doc["items"])
    assert doc["project"]["name"] == "Внедрение CRM в ООО «Ромашка»"


def test_суммарные_задачи_становятся_группами_с_вложенностью(doc):
    phase = row(doc, 16)
    assert phase["type"] == "group" and phase["parentKey"] is None and phase["color"] == "slate"
    assert row(doc, 18)["type"] == "group" and row(doc, 18)["parentKey"] == "i16"
    assert row(doc, 19)["parentKey"] == "i18" and row(doc, 22)["parentKey"] == "i16"
    assert row(doc, 42)["parentKey"] is None
    assert "start" not in phase


def test_порядок_строк_как_в_файле(doc):
    positions = [i["position"] for i in doc["items"]]
    assert positions == sorted(positions) and positions[0] == 1024.0
    assert [i["extra"]["msp"]["uid"] for i in doc["items"]][:5] == [1, 2, 3, 4, 5]


def test_веха_по_признаку_дата_начала(doc):
    milestone = row(doc, 4)
    assert milestone["type"] == "milestone" and milestone["date"] == "2026-03-06" and milestone["color"] == "teal"
    assert "start" not in milestone and "status" not in milestone


def test_веха_по_нулевой_длительности(doc):
    zero = row(doc, 44)
    assert zero["type"] == "milestone" and zero["date"] == "2026-04-30"


def test_задача_даты_из_файла(doc):
    t = row(doc, 6)
    assert (t["type"], t["start"], t["end"]) == ("task", "2026-03-10", "2026-03-20")
    assert (t["endMode"], t["duration"]) == ("date", None)
    assert t["offset"] == 0 and t["durationType"] == "calendar"


def test_окончание_в_начале_дня_предыдущий_день(doc):
    # SF-связь: окончание «Отчётов по продажам» — 29.04 08:00, это конец рабочего дня 28.04
    assert row(doc, 21)["end"] == "2026-04-28"
    assert row(doc, 2)["end"] == "2026-03-02"  # окончание в 17:00 — тот же день


def test_окончание_в_начале_дня_по_календарю_проекта():
    six = map_project(read_file(SIX_DAY))
    # 01.02 (пн) 09:00 при начале дня 09:00 — конец предыдущего рабочего дня: суббота 30.01 (неделя из 6 дней),
    # а не воскресенье (0.7.1)
    assert row(six, 2)["end"] == "2027-01-30"
    assert row(six, 3)["end"] == "2027-01-30"   # 30.01 10:00 — тот же день
    assert row(six, 1)["end"] == "2027-01-16"


def test_длительности_в_рабочих_днях(crm):
    working = map_project(crm, {**OPTIONS, "durations": "working"})
    t = row(working, 6)
    assert (t["endMode"], t["duration"], t["durationType"], t["end"]) == ("duration", 9, "working", "2026-03-20")
    assert row(working, 20)["duration"] == 6  # через 1 мая
    assert warning(working, "working_dates_differ") is None
    other = map_project(crm, {**OPTIONS, "durations": "working", "calendar": {"holidays": ["2026-03-12"]}})
    assert "Интервью с подразделениями" in warning(other, "working_dates_differ")["examples"]


def test_прошедшая_длительность_в_календарных_днях():
    data = mspdi(task(1, "Сушка бетона", "2026-03-02T08:00:00", "2026-03-07T08:00:00",
                      duration="PT120H0M0S", body="<DurationFormat>8</DurationFormat>"))
    t = mapped(data, durations="working")["items"][0]
    assert (t["end"], t["duration"], t["durationType"]) == ("2026-03-06", 5, "calendar")


def test_сроки_по_базовому_плану_и_сдвиг(crm):
    base = map_project(crm, {**OPTIONS, "baseline": "baseline"})
    t = row(base, 6)
    assert (t["start"], t["end"], t["offset"]) == ("2026-03-10", "2026-03-19", 1)
    assert (row(base, 31)["end"], row(base, 31)["offset"]) == ("2026-06-03", 5)
    assert row(base, 2)["offset"] == 0
    assert "Отчёт о результатах эксплуатации" in warning(base, "no_baseline")["examples"]
    assert row(base, 43)["start"] == "2026-07-28" and row(base, 43)["offset"] == 0


def test_базовый_план_0_в_сведениях(doc):
    assert doc["baseline"]["number"] == 0
    assert doc["baseline"]["items"]["i6"] == {"start": "2026-03-10", "end": "2026-03-19"}
    assert doc["baseline"]["items"]["i4"] == {"date": "2026-03-06"}


def test_неактивная_задача_пропускается(doc, crm):
    assert all(i["extra"]["msp"]["uid"] != 24 for i in doc["items"])
    assert warning(doc, "inactive_tasks")["examples"] == ["Старая выгрузка из Excel"]
    assert doc["stats"]["skipped"]["inactive"] == 1
    with_inactive = map_project(crm, {**OPTIONS, "skipInactive": False})
    assert row(with_inactive, 24)["parentKey"] == "i16" and warning(with_inactive, "inactive_tasks") is None


def test_пустая_строка_пропускается(doc):
    assert all(i["extra"]["msp"]["uid"] != 25 for i in doc["items"])
    assert warning(doc, "null_tasks")["count"] == 1


def test_повторяющаяся_задача_группа_с_вхождениями(doc):
    recurring = row(doc, 34)
    assert recurring["type"] == "group" and recurring["parentKey"] == "i33"
    occurrences = [i for i in doc["items"] if i["parentKey"] == "i34"]
    assert [i["name"] for i in occurrences] == [f"Еженедельное статус-совещание {n}" for n in (1, 2, 3)]
    assert [i["start"] for i in occurrences] == ["2026-06-15", "2026-06-22", "2026-06-29"]
    assert warning(doc, "recurring_tasks")["count"] == 1


def test_внешняя_задача_и_связи_с_другим_проектом_пропускаются(doc):
    assert all(i["extra"]["msp"]["uid"] != 39 for i in doc["items"])
    assert warning(doc, "external_tasks")["examples"] == ["Поставка серверов"]
    cross = warning(doc, "cross_project_links")
    assert cross["count"] == 2 and "Поставка серверов → Опытная эксплуатация" in cross["examples"]
    assert all(x["to"] != "i40" or x["from"] == "i38" for x in doc["links"])


def test_строка_без_названия():
    data = mspdi(task(1, ""))
    document = mapped(data)
    assert document["items"][0]["name"] == "Без названия" and warning(document, "empty_names")["count"] == 1


def test_строки_без_дат_пропускаются():
    no_dates = "<Task><UID>2</UID><ID>2</ID><Name>Без дат</Name><OutlineLevel>1</OutlineLevel></Task>"
    data = mspdi(task(1, "Есть даты") + no_dates)
    document = mapped(data)
    assert [i["name"] for i in document["items"]] == ["Есть даты"]
    assert warning(document, "invalid_dates")["examples"] == ["Без дат"]


# ================================================================ сопоставление: прогресс, поля
def test_прогресс_и_статус(doc):
    assert (row(doc, 8)["status"], row(doc, 8)["progress"], row(doc, 8)["completedDate"]) == ("new", 0, None)
    assert (row(doc, 6)["status"], row(doc, 6)["progress"]) == ("inwork", 60)
    done = row(doc, 3)
    assert (done["status"], done["progress"], done["completedDate"]) == ("done", 100, "2026-03-06")


def test_завершённая_задача_без_факта_дата_окончания():
    data = mspdi(task(1, "Готово", body="<PercentComplete>100</PercentComplete>"))
    assert mapped(data)["items"][0]["completedDate"] == "2026-03-06"


def test_крайний_срок(doc):
    assert row(doc, 9)["deadline"] == "2026-03-27"
    assert row(doc, 31)["deadline"] == "2026-06-05" and row(doc, 41)["deadline"] == "2026-06-30"
    assert row(doc, 8)["deadline"] is None


def test_ограничения(doc):
    assert (row(doc, 11)["constraintType"], row(doc, 11)["constraintDate"]) == ("snet", "2026-03-30")
    assert (row(doc, 15)["constraintType"], row(doc, 15)["constraintDate"]) == ("fnlt", "2026-04-24")
    assert (row(doc, 6)["constraintType"], row(doc, 6)["constraintDate"]) == (None, None)
    info = warning(doc, "constraints_info")
    assert info["examples"] == ["Архитектура решения", "Контрольная точка бюджета"]


def test_все_типы_ограничений():
    rows = "".join(task(n + 1, f"Задача {n}", body=f"<ConstraintType>{n}</ConstraintType>"
                        "<ConstraintDate>2026-03-04T08:00:00</ConstraintDate>") for n in range(8))
    document = mapped(mspdi(rows))
    # «Начало не ранее» 04.03 у задачи, начатой 02.03, «Пульс» сдвинул бы — дата прижата к началу задачи
    assert [(i["constraintType"], i["constraintDate"]) for i in document["items"]] == [
        (None, None), ("alap", None), ("mso", "2026-03-04"), ("mfo", "2026-03-03"), ("snet", "2026-03-02"),
        ("snlt", "2026-03-04"), ("fnet", "2026-03-03"), ("fnlt", "2026-03-03")]
    assert warning(document, "constraints_adjusted")["examples"] == ["Задача 4"]


def test_ограничения_суммарных_задач_не_переносятся():
    data = mspdi(task(1, "Фаза", body="<Summary>1</Summary><Deadline>2026-03-06T17:00:00</Deadline>")
                 + task(2, "Работа", level=2))
    document = mapped(data)
    assert "deadline" not in document["items"][0] and warning(document, "group_constraints")["examples"] == ["Фаза"]


def test_категория_из_настраиваемого_поля(doc):
    assert doc["settings"]["categories"] == ["Инициация", "Обследование", "Проектирование", "Разработка",
                                              "Тестирование", "Запуск"]
    assert (row(doc, 2)["category"], row(doc, 2)["color"]) == ("Инициация", "blue")
    assert (row(doc, 6)["category"], row(doc, 6)["color"]) == ("Обследование", "green")
    assert (row(doc, 28)["category"], row(doc, 28)["color"]) == ("Тестирование", "red")
    assert (row(doc, 42)["category"], row(doc, 42)["color"]) == (None, "blue")
    assert doc["stats"]["categories"] == 6


def test_категория_по_псевдониму_и_флагу(crm):
    by_alias = map_project(crm, {"categoryField": "Этап"})
    assert row(by_alias, 6)["category"] == "Обследование"
    by_flag = map_project(crm, {"categoryField": "188743752"})
    assert row(by_flag, 22)["category"] == "Ключевая для заказчика" and row(by_flag, 23)["category"] is None
    assert by_flag["settings"]["categories"] == ["Ключевая для заказчика"]
    empty = map_project(crm, {"categoryField": "188743734"})
    assert warning(empty, "category_field_empty") and empty["settings"]["categories"] == []


def test_без_поля_категорий(crm):
    plain = map_project(crm)
    assert plain["settings"]["categories"] == [] and row(plain, 6)["category"] is None


def test_список_настраиваемых_полей(doc):
    assert doc["customFields"] == [
        {"fieldId": "188743731", "fieldName": "Text1", "alias": "Этап", "label": "Этап", "count": 21},
        {"fieldId": "188743752", "fieldName": "Flag1", "alias": "Ключевая для заказчика",
         "label": "Ключевая для заказчика", "count": 3},
    ]


def test_гиперссылка_jira_в_ссылку_строки(doc, crm):
    assert row(doc, 11)["jiraUrl"] == "https://jira.romashka.ru/browse/CRM-101"
    without = map_project(crm)
    assert row(without, 11)["jiraUrl"] is None
    assert "Ссылка: CRM-101 — https://jira.romashka.ru/browse/CRM-101" in comment(without, "i11")


def test_гиперссылка_не_jira_в_заметку(doc):
    assert row(doc, 12)["jiraUrl"] is None
    assert comment(doc, "i12").endswith(
        "Ссылка: Регламент согласования — https://wiki.romashka.ru/pages/viewpage.action?pageId=4242")
    assert warning(doc, "hyperlinks_in_notes")["examples"] == ["Согласование ТЗ с заказчиком"]


def test_заметка_rtf_без_форматирования(doc):
    assert comment(doc, "i3") == "Заметка из MS Project: Устав согласован с заказчиком.\nВерсия 2 от 06.03.2026"


def test_заметка_html_без_разметки(doc):
    assert comment(doc, "i7") == "Заметка из MS Project: Описать все процессы отдела продаж\nСрок — две недели"


def test_заметка_простым_текстом(doc):
    assert comment(doc, "i12").startswith(
        "Заметка из MS Project: Согласование по регламенту заказчика.\n"
        "Ответственный со стороны заказчика: Смирнова О. В.\n")


def test_снятие_форматирования():
    assert plain_text("{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}\\f0 \\'cf\\'f0\\'e8\\'e2\\'e5\\'f2\\par}") == "Привет"
    assert plain_text("{\\rtf1 \\{скобки\\} и \\\\\\par}") == "{скобки} и \\"
    assert plain_text("<p>a&amp;b</p><br/>c") == "a&b\nc"
    assert plain_text("  \r\n ") is None and plain_text(None) is None


def test_номер_структуры_uid_и_guid_в_extra(doc, crm):
    by_uid = {t.uid: t for t in crm.tasks}
    assert row(doc, 6)["extra"] == {"msp": {"uid": 6, "guid": by_uid[6].guid, "wbs": "2.1", "outlineNumber": "2.1"}}
    assert row(doc, 19)["extra"]["msp"]["outlineNumber"] == "4.2.1"


def test_приоритет_затраты_и_календари_задач_в_предупреждениях(doc):
    for code in ("priority_not_transferred", "costs_not_transferred", "task_calendars_not_transferred"):
        assert warning(doc, code)["examples"] == ["Исправление замечаний"]


# ================================================================ сопоставление: связи
def test_связи_всех_типов(doc):
    assert link(doc, "l2-3")["type"] == "FS" and link(doc, "l6-7")["type"] == "SS"
    assert link(doc, "l7-8")["type"] == "FF" and link(doc, "l20-21")["type"] == "SF"
    assert link(doc, "l2-3") == {"key": "l2-3", "from": "i2", "to": "i3", "type": "FS", "lag": 0, "lagKind": "working"}
    assert doc["stats"]["links"] == len(doc["links"]) == 29


def test_лаг_в_рабочих_единицах(doc):
    assert (link(doc, "l6-7")["lag"], link(doc, "l6-7")["lagKind"]) == (3, "working")     # 3д
    assert link(doc, "l11-12")["lag"] == 0     # +4ч после окончания в 17:00 — следующее утро
    assert link(doc, "l15-22")["lag"] == 5     # 1н
    assert link(doc, "l41-43")["lag"] == 20    # 1мес (20 дней в месяце)
    assert link(doc, "l22-23")["lag"] == 2     # 2д? — оценка
    assert link(doc, "l28-29")["lag"] == 2
    assert link(doc, "l17-19")["lag"] == -1    # опережение
    assert (link(doc, "l20-21")["lag"], link(doc, "l20-21")["lagKind"]) == (-1, "working")  # SF


def test_лаг_в_прошедших_единицах(doc):
    assert (link(doc, "l7-8")["lag"], link(doc, "l7-8")["lagKind"]) == (2, "calendar")    # 2ед
    # 36еч после вехи 21.05 (чт) 17:00 — суббота 05:00; MS Project начинает задачу в понедельник 25.05:
    # лаг подобран под дату файла
    assert (link(doc, "l26-28")["lag"], link(doc, "l26-28")["lagKind"]) == (3, "calendar")
    assert warning(doc, "lags_adjusted")["examples"] == ["Разработка завершена → Функциональное тестирование"]


def test_лаг_в_процентах_от_длительности_предшественника(doc):
    assert (link(doc, "l11-14")["lag"], link(doc, "l11-14")["lagKind"]) == (5, "working")  # 50 % от 10 дней


@pytest.mark.parametrize("kind, tenths, fmt, expected", [
    (1, 4800, 3, (1, "working")),        # 480м = 1 день
    (1, 2400, 5, (0, "working")),        # FS +4ч — следующее утро
    (1, -2400, 5, (-1, "working")),      # FS −4ч — тот же день
    (1, 7200, 5, (1, "working")),        # FS +12ч
    (3, 2400, 5, (0, "working")),        # SS +4ч — тот же день
    (0, 2400, 5, (1, "working")),        # FF +4ч — окончание на следующий день
    (0, -2400, 5, (0, "working")),       # FF −4ч — тот же день
    (2, 0, 7, (-1, "working")),          # SF 0 — окончание накануне начала предшественника
    (2, 9600, 7, (1, "working")),        # SF +2д
    (1, 6000, 6, (0, "calendar")),       # FS +10еч
    (1, 21600, 6, (1, "calendar")),      # FS +36еч
    (0, 28800, 8, (2, "calendar")),      # FF +2ед
    (1, 100800, 10, (7, "calendar")),    # FS +1ен
    (1, 0, 21, (0, "working")),          # без лага
    (1, 24000, 41, (5, "working")),      # 1н? — оценка
])
def test_перевод_лага_в_дни(kind, tenths, fmt, expected):
    assert convert_lag(Link(1, 2, kind, tenths, fmt), 4800, 480) == expected


def test_лаг_в_процентах_и_месяцах():
    assert convert_lag(Link(1, 2, 1, 50, 19), 4800, 480) == (5, "working")      # 50 % от 10 дней
    assert convert_lag(Link(1, 2, 1, 25, 20), 4800, 480) == (2, "working")      # 25 % от 10 дней — 2,5 → 2
    assert convert_lag(Link(1, 2, 1, 96000, 11), None, 480) == (20, "working")  # 1мес = 20 дней


def test_округление_половины_от_нуля():
    assert [round_half(v) for v in (0.5, 1.5, 2.5, -0.5, -1.5, 0.49)] == [1, 2, 3, -1, -2, 0]


def test_связь_суммарных_задач_переносится_на_вложенные(doc):
    # «Обследование» → «Проектирование»: последняя по окончанию вложенная → первая по началу
    assert link(doc, "l9-11")["type"] == "FS"
    assert link(doc, "l5-10") is None
    # «Модуль продаж» → веха: последняя по окончанию — «Воронка продаж»
    assert link(doc, "l20-26") is not None
    assert warning(doc, "summary_links")["examples"] == ["Обследование → Проектирование",
                                                         "Модуль продаж → Разработка завершена"]


def test_связь_суммарной_по_началу_берёт_первую_вложенную():
    rows = (task(1, "Фаза", "2026-03-02T08:00:00", "2026-03-13T17:00:00", body="<Summary>1</Summary>")
            + task(2, "Поздняя", "2026-03-09T08:00:00", "2026-03-13T17:00:00", level=2)
            + task(3, "Ранняя", "2026-03-02T08:00:00", "2026-03-06T17:00:00", level=2)
            + task(4, "После", "2026-03-03T08:00:00", "2026-03-10T17:00:00", body=pred(1, kind=3, lag=4800)))
    links = mapped(mspdi(rows))["links"]
    assert [(x["from"], x["to"], x["type"], x["lag"]) for x in links] == [("i3", "i4", "SS", 1)]


def test_повторяющиеся_связи_объединяются(doc):
    assert warning(doc, "duplicate_links")["examples"] == ["Воронка продаж → Разработка завершена"]
    assert sum(1 for x in doc["links"] if (x["from"], x["to"]) == ("i20", "i26")) == 1


def test_связи_с_пропущенными_строками(doc):
    assert warning(doc, "links_to_skipped")["examples"] == ["Интеграция с 1С → Старая выгрузка из Excel"]


def test_связь_суммарной_без_вложенных_пропускается():
    rows = task(1, "Пустая фаза", body="<Summary>1</Summary>") + task(2, "Задача", body=pred(1))
    document = mapped(mspdi(rows))
    assert document["links"] == [] and warning(document, "summary_links_skipped")["count"] == 1


def test_цикл_связей_разрывается():
    rows = (task(1, "А", body=pred(3)) + task(2, "Б", body=pred(1)) + task(3, "В", body=pred(2))
            + task(4, "Г", body=pred(3)))
    document = mapped(mspdi(rows))
    assert [(x["from"], x["to"]) for x in document["links"]] == [("i3", "i1"), ("i1", "i2"), ("i3", "i4")]
    assert warning(document, "cyclic_links")["examples"] == ["Б → В"]


# ================================================================ сопоставление: исполнители
def test_ресурсы_работники_становятся_исполнителями(doc):
    assert [(a["key"], a["name"], a["role"]) for a in doc["assignees"]] == [
        ("a1", "Иванов Иван Иванович", "Руководитель проекта"), ("a2", "Петрова Анна Сергеевна", "Аналитик"),
        ("a3", "Сёмин Пётр Алексеевич", "Разработчик")]
    assert [a["color"] for a in doc["assignees"]] == ["#3b82f6", "#14b8a6", "#22c55e"]
    assert [a["position"] for a in doc["assignees"]] == [1.0, 2.0, 3.0]


def test_связь_с_сотрудником_по_email_затем_по_фио(doc):
    by_key = {a["key"]: a["userId"] for a in doc["assignees"]}
    assert by_key == {"a1": 7, "a2": None, "a3": 8}   # email; нет; ФИО с «ё» и лишним пробелом
    assert doc["stats"]["linkedUsers"] == 2


def test_одинаковые_фио_сотрудников_не_связываются():
    data = mspdi(task(1, "Задача"), resources=resource(1, "Петров Пётр"), assignments=assignment(1, 1))
    document = mapped(data, users=[{"id": 1, "name": "Петров Петр"}, {"id": 2, "name": "ПЕТРОВ ПЁТР"}])
    assert document["assignees"][0]["userId"] is None
    assert warning(document, "ambiguous_users")["examples"] == ["Петров Пётр"]


def test_материальные_и_затратные_ресурсы_не_переносятся(doc):
    assert warning(doc, "material_resources")["examples"] == ["Лицензии CRM"]
    data = mspdi(task(1, "Задача"), resources=resource(1, "Командировки", kind=2), assignments=assignment(1, 1))
    document = mapped(data)
    assert document["assignees"] == [] and document["items"][0]["assigneeKey"] is None


def test_исполнитель_строки_по_назначению(doc):
    assert row(doc, 3)["assigneeKey"] == "a2" and row(doc, 4)["assigneeKey"] == "a1"  # и у вехи
    assert row(doc, 43)["assigneeKey"] is None


def test_несколько_назначений_больше_трудозатрат_остальные_в_заметке(doc):
    assert row(doc, 6)["assigneeKey"] == "a1"
    assert comment(doc, "i6") == "Заметка из MS Project: Также назначены: Петрова Анна Сергеевна (36 ч)"
    assert row(doc, 22)["assigneeKey"] == "a3"
    assert comment(doc, "i22") == ("Заметка из MS Project: Обмен через шину данных заказчика\n"
                                   "Также назначены: Иванов Иван Иванович (40 ч)")
    assert warning(doc, "multiple_assignments")["count"] == 3


def test_назначения_суммарных_задач_не_переносятся(doc):
    assert warning(doc, "group_assignments")["examples"] == ["Разработка и настройка"]
    assert "assigneeKey" not in row(doc, 16)


# ================================================================ сопоставление: план целиком
def test_шкала_и_источник_плана(doc, crm):
    assert doc["settings"]["range"] == {"start": "2026-02-23", "end": "2026-08-12"}
    assert doc["settings"]["source"] == {"kind": "msproject", "projectGuid": crm.guid}
    assert doc["project"] == {"name": "Внедрение CRM в ООО «Ромашка»", "title": "Внедрение CRM в ООО «Ромашка»",
                              "guid": crm.guid, "start": "2026-03-02", "finish": "2026-07-29"}


def test_календарь_проекта_в_сведениях(doc):
    calendar = doc["calendar"]
    assert calendar["name"] == "Стандартный" and calendar["workWeek"] == [1, 2, 3, 4, 5]
    assert calendar["dayStart"] == "08:00" and calendar["minutesPerDay"] == 480
    assert {"date": "2026-03-09", "working": False, "title": "Перенос выходного (с 8 марта)"} in calendar["exceptions"]
    assert sum(1 for e in calendar["exceptions"] if e["title"] == "Новогодние каникулы") == 8


def test_шестидневка_и_повторяющиеся_исключения():
    six = map_project(read_file(SIX_DAY))
    calendar = six["calendar"]
    assert calendar["workWeek"] == [1, 2, 3, 4, 5, 6] and calendar["dayStart"] == "09:00"
    days = {e["date"]: (e["working"], e["title"]) for e in calendar["exceptions"]}
    assert days["2027-01-04"] == (False, "Новогодние каникулы")
    assert days["2027-02-28"] == (True, "Рабочее воскресенье")
    assert days["2027-06-12"] == (False, "День России") and "2028-06-12" not in days  # дальше шкалы плана + год
    assert [d for d, (_, t) in sorted(days.items()) if t == "Санитарный день"] == [
        "2027-01-29", "2027-02-26", "2027-03-26"]
    assert [d for d, (_, t) in sorted(days.items()) if t == "Профилактика оборудования"] == [
        "2027-03-01", "2027-03-15", "2027-03-29"]


def test_итоги_и_предупреждения(doc):
    assert doc["stats"] == {
        "groups": 8, "tasks": 26, "milestones": 7, "links": 29, "assignees": 3, "linkedUsers": 2, "comments": 6,
        "categories": 6, "skipped": {"inactive": 1, "null": 1, "external": 1, "invalidDates": 0,
                                     "crossProjectLinks": 2, "linksToSkipped": 1, "summaryLinks": 0, "cyclicLinks": 0,
                                     "materialResources": 1}}
    codes = [w["code"] for w in doc["warnings"]]
    assert codes == ["inactive_tasks", "null_tasks", "external_tasks", "recurring_tasks", "cross_project_links",
                     "links_to_skipped", "summary_links", "duplicate_links", "lags_adjusted", "constraints_info",
                     "multiple_assignments", "group_assignments", "material_resources", "hyperlinks_in_notes",
                     "priority_not_transferred", "costs_not_transferred", "task_calendars_not_transferred"]
    for entry in doc["warnings"]:
        assert set(entry) == {"code", "message", "count", "examples"} and entry["count"] >= 1
        assert len(entry["examples"]) <= 5 and entry["message"]


def test_документ_загрузки_json_и_ключи_повторяются(crm, doc):
    again = map_project(read_file(CRM), OPTIONS)
    assert json.dumps(again, ensure_ascii=False, sort_keys=True) == json.dumps(doc, ensure_ascii=False, sort_keys=True)
    assert import_document(CRM.read_bytes(), OPTIONS) == doc
    keys = [i["key"] for i in doc["items"]] + [x["key"] for x in doc["links"]] + [a["key"] for a in doc["assignees"]]
    assert len(keys) == len(set(keys))


def big_file(groups=250, per_group=20):
    """MSPDI на groups × per_group задач: фазы, цепочки FS, базовый план, настраиваемое поле, назначения."""
    parts = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Project xmlns="{NS}">'
             "<SaveVersion>14</SaveVersion><Name>Большой.xml</Name><Title>Большой проект</Title>"
             "<StartDate>2026-01-12T08:00:00</StartDate><CalendarUID>1</CalendarUID><MinutesPerDay>480</MinutesPerDay>"
             "<ExtendedAttributes><ExtendedAttribute><FieldID>188743731</FieldID><FieldName>Text1</FieldName>"
             "<Alias>Этап</Alias></ExtendedAttribute></ExtendedAttributes><Tasks>"
             "<Task><UID>0</UID><ID>0</ID><Name>Большой проект</Name><OutlineLevel>0</OutlineLevel>"
             "<Summary>1</Summary></Task>"]
    assignments, uid = [], 0
    start = date(2026, 1, 12)
    for g in range(groups):
        uid += 1
        parts.append(f"<Task><UID>{uid}</UID><GUID>00000000-0000-4000-8000-{uid:012d}</GUID><ID>{uid}</ID>"
                     f"<Name>Фаза {g + 1}</Name><OutlineNumber>{g + 1}</OutlineNumber><OutlineLevel>1</OutlineLevel>"
                     f"<Start>{start}T08:00:00</Start><Finish>{start}T17:00:00</Finish><Summary>1</Summary></Task>")
        previous = None
        for n in range(per_group):
            uid += 1
            day = start + timedelta(days=n % 30)
            body = pred(previous) if previous else ""
            parts.append(
                f"<Task><UID>{uid}</UID><GUID>00000000-0000-4000-8000-{uid:012d}</GUID><ID>{uid}</ID>"
                f"<Name>Работа {g + 1}.{n + 1}</Name><Active>1</Active><Manual>0</Manual><IsNull>0</IsNull>"
                f"<WBS>{g + 1}.{n + 1}</WBS><OutlineNumber>{g + 1}.{n + 1}</OutlineNumber>"
                f"<OutlineLevel>2</OutlineLevel><Priority>500</Priority><Start>{day}T08:00:00</Start>"
                f"<Finish>{day + timedelta(days=2)}T17:00:00</Finish><Duration>PT24H0M0S</Duration>"
                f"<DurationFormat>7</DurationFormat><Milestone>0</Milestone><Summary>0</Summary>"
                f"<PercentComplete>{(n * 7) % 101}</PercentComplete><ConstraintType>0</ConstraintType>"
                f"<CalendarUID>-1</CalendarUID>{body}<ExtendedAttribute><FieldID>188743731</FieldID>"
                f"<Value>Этап {g % 7}</Value></ExtendedAttribute><Baseline><Number>0</Number>"
                f"<Start>{day}T08:00:00</Start><Finish>{day + timedelta(days=2)}T17:00:00</Finish>"
                f"<Duration>PT24H0M0S</Duration></Baseline></Task>")
            assignments.append(assignment(uid, 1 + uid % 5, "PT24H0M0S"))
            previous = uid
    parts.append("</Tasks><Resources>")
    parts += [resource(r, f"Сотрудник {r}", email=f"user{r}@example.ru") for r in range(1, 6)]
    parts.append("</Resources><Assignments>" + "".join(assignments) + "</Assignments></Project>")
    return "".join(parts).encode()


def test_большой_файл_читается_и_сопоставляется_быстро():
    data = big_file()
    started = time.perf_counter()
    document = map_project(read_mspdi(data), {"categoryField": "188743731"})
    elapsed = time.perf_counter() - started
    assert elapsed < 3, f"чтение и сопоставление 5000 задач — {elapsed:.2f} с"
    assert document["stats"]["tasks"] == 5000 and document["stats"]["groups"] == 250
    assert document["stats"]["links"] == 250 * 19
    assert warning(document, "too_many_items")["count"] == 1 and len(document["items"]) > ITEM_LIMIT


# ================================================================ обновление из MS Project
def as_plan(document):
    """Документ загрузки → план «Пульса» с другими ключами — как после применения на сервере."""
    items_map = {i["key"]: "k" + i["key"][1:] for i in document["items"]}
    people = {a["key"]: "p" + a["key"][1:] for a in document["assignees"]}
    items = []
    for item in deepcopy(document["items"]):
        item["key"] = items_map[item["key"]]
        item["parentKey"] = items_map.get(item["parentKey"])
        if item.get("assigneeKey"):
            item["assigneeKey"] = people[item["assigneeKey"]]
        items.append(item)
    links = [{**x, "key": "x" + x["key"][1:], "from": items_map[x["from"]], "to": items_map[x["to"]]}
             for x in document["links"]]
    assignees = [{**a, "key": people[a["key"]]} for a in document["assignees"]]
    return {"items": items, "links": links, "assignees": assignees, "settings": deepcopy(document["settings"])}


def plan_row(plan, key):
    return next(i for i in plan["items"] if i["key"] == key)


def op_for(ops, kind, key):
    return [op for op in ops if op["op"] == kind and (op.get("key") == key or (op.get("item") or {}).get("key") == key
                                                       or (op.get("link") or {}).get("key") == key)]


def test_обновление_без_изменений(doc):
    changes = diff_plan(as_plan(doc), doc)
    assert changes.counts == {"new": 0, "changed": 0, "recreated": 0, "unchanged": 41, "missing": 0, "pulsOnly": 0,
                              "linksAdded": 0, "linksRemoved": 0, "linksChanged": 0, "assigneesAdded": 0,
                              "assigneesRenamed": 0,
                              "deletedInPuls": 0}
    assert to_ops(changes) == [] and changes.warnings == []
    assert changes.key_map["i6"] == "k6" and changes.assignees["matched"] == {"a1": "p1", "a2": "p2", "a3": "p3"}


def test_обновление_переименование(crm, doc):
    renamed = replace(crm, tasks=tuple(replace(t, name="Интервью с отделами") if t.uid == 6 else t for t in crm.tasks))
    changes = diff_plan(as_plan(doc), map_project(renamed, OPTIONS))
    assert [(c["key"], c["fields"]) for c in changes.changed] == [
        ("k6", {"name": {"before": "Интервью с подразделениями", "after": "Интервью с отделами"}})]
    assert to_ops(changes) == [{"op": "item.update", "key": "k6", "fields": {"name": "Интервью с отделами"}}]


def test_обновление_перенос_в_другую_суммарную_задачу(doc):
    moved = deepcopy(doc)
    row(moved, 14)["parentKey"] = "i16"
    items = moved["items"]
    interviews = items.pop(next(n for n, i in enumerate(items) if i["key"] == "i14"))
    items.insert(next(n for n, i in enumerate(items) if i["key"] == "i22"), interviews)
    changes = diff_plan(as_plan(doc), moved)
    entry = next(c for c in changes.changed if c["key"] == "k14")
    assert entry["fields"]["parentKey"] == {"before": "k10", "after": "k16"}
    plan = as_plan(doc)
    position = entry["fields"]["position"]["after"]
    assert plan_row(plan, "k18")["position"] < position < plan_row(plan, "k22")["position"]
    assert {c["key"] for c in changes.changed} == {"k14"}


def test_обновление_сроки_и_прогресс(doc):
    later = deepcopy(doc)
    row(later, 8).update({"start": "2026-03-25", "end": "2026-03-27", "progress": 100, "status": "done",
                          "completedDate": "2026-03-27"})
    changes = diff_plan(as_plan(doc), later)
    assert changes.changed[0]["fields"] == {
        "start": {"before": "2026-03-24", "after": "2026-03-25"},
        "end": {"before": "2026-03-26", "after": "2026-03-27"},
        "progress": {"before": 0, "after": 100}, "status": {"before": "new", "after": "done"},
        "completedDate": {"before": None, "after": "2026-03-27"}}


def test_обновление_новая_задача_встаёт_на_место(doc):
    grown = deepcopy(doc)
    new_row = deepcopy(row(doc, 12))
    new_row.update({"key": "i100", "name": "Повторное согласование ТЗ", "position": 12800.0,
                    "extra": {"msp": {"uid": 100, "guid": "11111111-2222-3333-4444-555555555555", "wbs": "3.3",
                                      "outlineNumber": "3.3"}}})
    grown["items"].insert(next(n for n, i in enumerate(grown["items"]) if i["key"] == "i13"), new_row)
    grown["links"].append({"key": "l12-100", "from": "i12", "to": "i100", "type": "FS", "lag": 0, "lagKind": "working"})
    grown["comments"].append({"itemKey": "i100", "text": "Заметка из MS Project: по замечаниям"})
    changes = diff_plan(as_plan(doc), grown)
    plan = as_plan(doc)
    assert [c["key"] for c in changes.new] == ["i100"] and changes.counts["changed"] == 0
    created = changes.new[0]["item"]
    assert created["parentKey"] == "k10" and created["assigneeKey"] == "p1"
    assert plan_row(plan, "k12")["position"] < created["position"] < plan_row(plan, "k13")["position"]
    assert changes.links["added"] == [{"key": "l12-100", "from": "k12", "to": "i100", "type": "FS", "lag": 0,
                                       "lagKind": "working"}]
    assert changes.comments == [{"itemKey": "i100", "text": "Заметка из MS Project: по замечаниям"}]
    ops = to_ops(changes)
    assert [op["op"] for op in ops] == ["item.create", "link.create"]


def test_обновление_ключ_новой_строки_не_совпадает_с_занятым(doc):
    grown = deepcopy(doc)
    new_row = deepcopy(row(doc, 42))
    new_row.update({"key": "i100", "extra": {"msp": {"uid": 100, "guid": None, "wbs": None, "outlineNumber": None}}})
    grown["items"].append(new_row)
    changes = diff_plan(as_plan(doc), grown, reserved_keys={"i100", "i100x2"})
    assert changes.new[0]["key"] == "i100x3"


def test_обновление_задача_удалена_в_ms_project(doc):
    shrunk = deepcopy(doc)
    shrunk["items"] = [i for i in shrunk["items"] if i["key"] != "i29"]
    shrunk["links"] = [x for x in shrunk["links"] if "i29" not in (x["from"], x["to"])]
    changes = diff_plan(as_plan(doc), shrunk)
    assert [(m["key"], m["name"]) for m in changes.missing] == [("k29", "Нагрузочное тестирование")]
    assert changes.links["removed"] == []  # связь удалится вместе со строкой
    assert to_ops(changes) == []
    assert to_ops(changes, {"deleteMissing": True}) == [{"op": "item.delete", "key": "k29"}]


def test_обновление_связи(doc):
    relinked = deepcopy(doc)
    relinked["links"] = [x for x in relinked["links"] if x["key"] != "l12-13"]
    link(relinked, "l11-12")["lag"] = 3
    relinked["links"].append({"key": "l2-11", "from": "i2", "to": "i11", "type": "SS", "lag": 0, "lagKind": "working"})
    changes = diff_plan(as_plan(doc), relinked)
    assert changes.links["removed"] == [{"key": "x12-13", "from": "k12", "to": "k13", "type": "FS", "lag": 0,
                                         "lagKind": "working"}]
    assert changes.links["changed"] == [{"key": "x11-12", "from": "k11", "to": "k12",
                                         "fields": {"lag": {"before": 0, "after": 3}}}]
    assert to_ops(changes) == [
        {"op": "link.delete", "key": "x12-13"},
        {"op": "link.update", "key": "x11-12", "fields": {"lag": 3}},
        {"op": "link.create", "link": {"key": "l2-11", "from": "k2", "to": "k11", "type": "SS", "lag": 0,
                                       "lagKind": "working"}},
    ]


def test_обновление_не_трогает_поля_и_связи_пульса(doc):
    plan = as_plan(doc)
    plan_row(plan, "k7").update({"color": "red", "barText": "важно", "category": "Своя категория"})
    plan_row(plan, "k11")["jiraUrl"] = "https://jira.romashka.ru/browse/CRM-999"
    plan_row(plan, "k8")["category"] = None
    plan["items"].append({"key": "k500", "type": "task", "name": "Задача из «Пульса»", "parentKey": "k5",
                          "position": 99999.0, "start": "2026-03-10", "end": "2026-03-11"})
    plan["links"].append({"key": "x500", "from": "k8", "to": "k500", "type": "FS", "lag": 0, "lagKind": "calendar"})
    changes = diff_plan(plan, doc)
    assert [(c["key"], c["fields"]) for c in changes.changed] == [
        ("k8", {"category": {"before": None, "after": "Обследование"}})]
    assert changes.puls_only == [{"key": "k500", "type": "task", "name": "Задача из «Пульса»"}]
    assert changes.missing == [] and changes.links["removed"] == []
    ops = to_ops(changes, {"deleteMissing": True})
    assert ops == [{"op": "item.update", "key": "k8", "fields": {"category": "Обследование"}}]


def test_обновление_статус_заблокирована_сохраняется(doc):
    plan = as_plan(doc)
    plan_row(plan, "k6")["status"] = "blocked"
    assert diff_plan(plan, doc).changed == []
    finished = deepcopy(doc)
    row(finished, 6).update({"status": "done", "progress": 100, "completedDate": "2026-03-20"})
    fields = diff_plan(plan, finished).changed[0]["fields"]
    assert fields["status"] == {"before": "blocked", "after": "done"}


def test_обновление_новый_исполнитель(doc):
    staffed = deepcopy(doc)
    staffed["assignees"].append({"key": "a9", "name": "Козлов Олег Игоревич", "role": "Тестировщик",
                                 "color": "#f97316", "userId": 9, "position": 4.0})
    row(staffed, 8)["assigneeKey"] = "a9"
    changes = diff_plan(as_plan(doc), staffed)
    assert changes.assignees["added"] == [{"key": "a9", "name": "Козлов Олег Игоревич", "role": "Тестировщик",
                                           "color": "#f97316", "userId": 9, "position": 4.0}]
    ops = to_ops(changes)
    assert ops[0]["op"] == "assignee.create" and ops[0]["assignee"]["key"] == "a9"
    assert ops[1] == {"op": "item.update", "key": "k8", "fields": {"assigneeKey": "a9"}}


def test_обновление_файл_без_ресурсов_не_снимает_исполнителей(doc):
    bare = deepcopy(doc)
    bare["assignees"] = []
    for item in bare["items"]:
        if "assigneeKey" in item:
            item["assigneeKey"] = None
    assert diff_plan(as_plan(doc), bare).changed == []


def test_обновление_смена_типа_строки(doc):
    retyped = deepcopy(doc)
    old = row(retyped, 42)
    milestone = {k: old[k] for k in ("key", "name", "parentKey", "position", "assigneeKey", "jiraUrl", "deadline",
                                     "constraintType", "constraintDate", "extra")}
    milestone.update({"type": "milestone", "date": "2026-06-30", "color": "teal"})
    retyped["items"][retyped["items"].index(old)] = milestone
    changes = diff_plan(as_plan(doc), retyped)
    entry = changes.changed[0]
    assert entry["recreate"] and entry["key"] == "k42" and entry["newKey"] == "i42"
    assert changes.links["added"] == [{"key": "l41-42", "from": "k41", "to": "i42", "type": "FS", "lag": 0,
                                       "lagKind": "working"}]
    assert changes.missing == [] and changes.warnings[0]["code"] == "type_changed"
    ops = to_ops(changes)
    assert ops[:2] == [{"op": "item.delete", "key": "k42"}, {"op": "item.create", "item": entry["item"]}]
    assert entry["item"]["type"] == "milestone" and entry["item"]["key"] == "i42"
    assert [op["op"] for op in ops[2:]] == ["link.create"]


def test_обновление_сопоставление_по_uid_для_файла_без_guid(doc):
    no_guid = deepcopy(doc)
    for item in no_guid["items"]:
        item["extra"]["msp"]["guid"] = None
    changes = diff_plan(as_plan(doc), no_guid)
    assert changes.counts["new"] == 0 and changes.counts["changed"] == 0 and changes.counts["missing"] == 0
    assert len(changes.meta) == 41  # GUID в extra.msp обновится, строка в изменения не попадает
    ops = to_ops(changes)
    assert all(op["op"] == "item.update" and set(op["fields"]) == {"extra"} for op in ops)


def test_обновление_uid_с_другим_guid_не_сопоставляется(doc):
    other = deepcopy(doc)
    row(other, 42)["extra"]["msp"]["guid"] = "99999999-9999-9999-9999-999999999999"
    changes = diff_plan(as_plan(doc), other)
    assert [c["importKey"] for c in changes.new] == ["i42"] and [m["key"] for m in changes.missing] == ["k42"]


def test_обновление_файл_другого_проекта(doc):
    plan = as_plan(doc)
    plan["settings"]["source"]["projectGuid"] = "AAAAAAAA-0000-0000-0000-000000000000"
    assert diff_plan(plan, doc).warnings[0]["code"] == "other_project"


def test_обновление_сдвиг_по_базовому_плану(crm, doc):
    base = map_project(crm, {**OPTIONS, "baseline": "baseline"})
    changes = diff_plan(as_plan(doc), base)
    entry = next(c for c in changes.changed if c["key"] == "k6")
    assert entry["fields"]["offset"] == {"before": 0, "after": 1} and entry["fields"]["end"]["after"] == "2026-03-19"
    current = deepcopy(doc)
    row(current, 6)["offset"] = 5   # сдвиг, заданный в «Пульсе», загрузка по текущим срокам не трогает
    assert diff_plan(as_plan(current), doc).changed == []


def test_обновление_операции_детерминированы(crm, doc):
    renamed = replace(crm, tasks=tuple(replace(t, name=t.name + " (изм.)") if t.uid in (3, 30) else t
                                       for t in crm.tasks if t.uid != 29))
    target = map_project(renamed, OPTIONS)
    first = to_ops(diff_plan(as_plan(doc), target), {"deleteMissing": True})
    second = to_ops(diff_plan(as_plan(doc), target), {"deleteMissing": True})
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    changes = diff_plan(as_plan(doc), target)
    assert to_ops(json.loads(json.dumps(changes.to_dict())), {"deleteMissing": True}) == first
    assert [op["op"] for op in first] == ["item.update", "item.update", "item.delete"]


# ================================================================ экспорт
XSD = {
    "Project": "SaveVersion UID Name GUID Title Subject Category Company Manager Author CreationDate Revision "
               "LastSaved ScheduleFromStart StartDate FinishDate FYStartDate CriticalSlackLimit CurrencyDigits "
               "CurrencySymbol CurrencyCode CurrencySymbolPosition CalendarUID DefaultStartTime DefaultFinishTime "
               "MinutesPerDay MinutesPerWeek DaysPerMonth DefaultTaskType DefaultFixedCostAccrual DefaultStandardRate "
               "DefaultOvertimeRate DurationFormat WorkFormat EditableActualCosts HonorConstraints EarnedValueMethod "
               "InsertedProjectsLikeSummary MultipleCriticalPaths NewTasksEffortDriven NewTasksEstimated "
               "SplitsInProgressTasks SpreadActualCost SpreadPercentComplete TaskUpdatesResource FiscalYearStart "
               "WeekStartDay MoveCompletedEndsBack MoveRemainingStartsBack MoveRemainingStartsForward "
               "MoveCompletedEndsForward BaselineForEarnedValue AutoAddNewResourcesAndTasks StatusDate CurrentDate "
               "MicrosoftProjectServerURL Autolink NewTaskStartDate NewTasksAreManual DefaultTaskEVMethod "
               "ProjectExternallyEdited ExtendedCreationDate ActualsInSync RemoveFileProperties AdminProject "
               "BaselineCalendar UpdateManuallyScheduledTasksWhenEditingLinks "
               "KeepTaskOnNearestWorkingTimeWhenMadeAutoScheduled OutlineCodes WBSMasks ExtendedAttributes Calendars "
               "Tasks Resources Assignments",
    "Calendar": "UID GUID Name IsBaseCalendar IsBaselineCalendar BaseCalendarUID WeekDays Exceptions WorkWeeks",
    "WeekDay": "DayType DayWorking TimePeriod WorkingTimes",
    "Exception": "EnteredByOccurrences TimePeriod Occurrences Name Type Period DaysOfWeek MonthItem MonthPosition "
                 "Month MonthDay DayWorking WorkingTimes",
    "Task": "UID GUID ID Name Active Manual Type IsNull CreateDate Contact WBS WBSLevel OutlineNumber OutlineLevel "
            "Priority Start Finish Duration DurationFormat Work Stop Resume ResumeValid EffortDriven Recurring "
            "OverAllocated Estimated Milestone Summary DisplayAsSummary Critical IsSubproject IsSubprojectReadOnly "
            "SubprojectName ExternalTask ExternalTaskProject EarlyStart EarlyFinish LateStart LateFinish "
            "StartVariance FinishVariance WorkVariance FreeSlack TotalSlack StartSlack FinishSlack FixedCost "
            "FixedCostAccrual PercentComplete PercentWorkComplete Cost OvertimeCost OvertimeWork ActualStart "
            "ActualFinish ActualDuration ActualCost ActualOvertimeCost ActualWork ActualOvertimeWork RegularWork "
            "RemainingDuration RemainingCost RemainingWork RemainingOvertimeCost RemainingOvertimeWork ACWP CV "
            "ConstraintType CalendarUID ConstraintDate Deadline LevelAssignments LevelingCanSplit LevelingDelay "
            "LevelingDelayFormat PreLeveledStart PreLeveledFinish Hyperlink HyperlinkAddress HyperlinkSubAddress "
            "IgnoreResourceCalendar Notes HideBar Rollup BCWS BCWP PhysicalPercentComplete EarnedValueMethod "
            "PredecessorLink ActualWorkProtected ActualOvertimeWorkProtected ExtendedAttribute Baseline OutlineCode "
            "IsPublished StatusManager CommitmentStart CommitmentFinish CommitmentType StartText FinishText "
            "DurationText ManualStart ManualFinish ManualDuration TimephasedData",
    "PredecessorLink": "PredecessorUID Type CrossProject CrossProjectName LinkLag LagFormat",
    "Baseline": "TimephasedData Number Interim Start Finish Duration DurationFormat EstimatedDuration Work Cost BCWS "
                "BCWP FixedCost",
    "Resource": "UID GUID ID Name Type IsNull Initials Phonetics NTAccount MaterialLabel Code Group WorkGroup "
                "EmailAddress Hyperlink HyperlinkAddress HyperlinkSubAddress MaxUnits PeakUnits OverAllocated "
                "AvailableFrom AvailableTo Start Finish CanLevel AccrueAt Work",
    "Assignment": "UID GUID TaskUID ResourceUID PercentWorkComplete ActualCost ActualFinish ActualOvertimeCost "
                  "ActualOvertimeWork ActualStart ActualWork ACWP Confirmed Cost CostRateTable CostVariance CV Delay "
                  "Finish FinishVariance Hyperlink HyperlinkAddress HyperlinkSubAddress WorkVariance "
                  "HasFixedRateUnits FixedMaterial LevelingDelay LevelingDelayFormat LinkedFields Milestone Notes "
                  "Overallocated OvertimeCost OvertimeWork PeakUnits RateScale RegularWork RemainingCost "
                  "RemainingOvertimeCost RemainingOvertimeWork RemainingWork ResponsePending Start Stop Resume "
                  "StartVariance Summary SV Units UpdateNeeded VAC Work WorkContour",
}


def local(tag):
    return tag.rsplit("}", 1)[-1]


def check_order(element, path=""):
    """Дочерние элементы идут в порядке схемы MSPDI (повторы одного элемента — подряд)."""
    name = local(element.tag)
    if name == "ExtendedAttribute" and path.endswith("Task"):
        order = ["FieldID", "Value", "ValueGUID", "DurationFormat"]
    elif name == "ExtendedAttribute":
        order = ["FieldID", "FieldName", "CFType", "Guid", "ElemType", "MaxMultiValues", "UserDef", "Alias"]
    else:
        order = XSD.get(name, "").split()
    if order:
        indexes = [order.index(local(child.tag)) for child in element]
        assert indexes == sorted(indexes), f"{path}/{name}: {[local(c.tag) for c in element]}"
    for child in element:
        check_order(child, f"{path}/{name}")


def written(document, **kwargs):
    return ET.fromstring(write_mspdi(document, **kwargs))


def xml_task(root, uid):
    return next(t for t in root.iter(Q + "Task") if t.findtext(Q + "UID") == str(uid))


def puls_plan():
    """План, созданный в «Пульсе»: без extra.msp, с задачей с подзадачей и связями в рабочих и календарных днях."""
    return {
        "plan": {"key": "pl-demo", "title": "План релиза"},
        "items": [
            {"key": "g1", "type": "group", "name": "Релиз 2.0", "parentKey": None, "position": 1},
            {"key": "t1", "type": "task", "name": "Разработка", "parentKey": "g1", "position": 2, "start": "2026-03-02",
             "end": "2026-03-06", "status": "inwork", "progress": 40, "category": "разработка",
             "jiraUrl": "https://jira.romashka.ru/browse/REL-7", "assigneeKey": "aA", "offset": 2},
            {"key": "t2", "type": "task", "name": "Тестирование", "parentKey": "g1", "position": 3,
             "start": "2026-03-10", "end": "2026-03-12", "status": "done", "progress": 100,
             "completedDate": "2026-03-13", "deadline": "2026-03-13", "constraintType": "snet",
             "constraintDate": "2026-03-10", "assigneeKey": "aB"},
            {"key": "t3", "type": "task", "name": "Сборка", "parentKey": "t1", "position": 4, "start": "2026-03-04",
             "end": "2026-03-05"},
            {"key": "m1", "type": "milestone", "name": "Релиз", "parentKey": None, "position": 5, "date": "2026-03-16",
             "constraintType": "mfo", "constraintDate": "2026-03-16"},
            {"key": "gone", "type": "task", "name": "Удалённая", "deleted": True, "start": "2026-03-02",
             "end": "2026-03-02"},
        ],
        "links": [
            {"key": "l1", "from": "t1", "to": "t2", "type": "FS", "lag": 2, "lagKind": "working"},
            {"key": "l2", "from": "t2", "to": "m1", "type": "SS", "lag": 3, "lagKind": "calendar"},
        ],
        "assignees": [{"key": "aB", "name": "Петрова Анна", "role": "QA", "userId": 2, "position": 2},
                      {"key": "aA", "name": "Иванов Иван", "role": "", "userId": 1, "position": 1}],
        "comments": [{"itemKey": "t2", "text": "Регресс по чек-листу"},
                     {"itemKey": "t2", "text": MS_NOTE_PREFIX + "из файла"}],
    }


def test_экспорт_порядок_элементов_по_схеме(doc):
    root = written(doc, holidays={"2026-03-09": "Перенос выходного"})
    assert root.tag == Q + "Project"
    check_order(root)
    check_order(written(puls_plan(), holidays=["2026-03-09"], workdays=["2026-03-14"], scheduling="auto"))


def test_экспорт_структура(doc):
    root = written(doc)
    tasks = root.findall(f"{Q}Tasks/{Q}Task")
    assert tasks[0].findtext(Q + "UID") == "0" and tasks[0].findtext(Q + "OutlineLevel") == "0"
    assert len(tasks) == 42 and [t.findtext(Q + "ID") for t in tasks] == [str(n) for n in range(42)]
    phase = xml_task(root, 16)
    assert [phase.findtext(Q + name) for name in ("Summary", "OutlineLevel", "Manual")] == ["1", "1", "0"]
    assert xml_task(root, 19).findtext(Q + "OutlineLevel") == "3"
    assert xml_task(root, 19).findtext(Q + "OutlineNumber") == "4.2.1"
    assert xml_task(root, 4).findtext(Q + "Milestone") == "1"
    assert root.findtext(Q + "Title") == "Внедрение CRM в ООО «Ромашка»"
    assert root.findtext(Q + "StartDate") == "2026-03-02T08:00:00"


def test_экспорт_сохраняет_guid_и_uid_из_ms_project(doc, crm):
    root = written(doc)
    by_uid = {t.uid: t for t in crm.tasks}
    assert xml_task(root, 6).findtext(Q + "GUID") == by_uid[6].guid
    assert xml_task(root, 22).findtext(Q + "WBS") == "4.3"
    # WBS — из файла, номер структуры — по дереву (неактивная задача и пустая строка не выгружаются)
    assert (xml_task(root, 26).findtext(Q + "WBS"), xml_task(root, 26).findtext(Q + "OutlineNumber")) == ("4.6", "4.5")
    assert root.findtext(Q + "GUID") == crm.guid


def test_экспорт_новых_строк_детерминирован():
    first, second = write_mspdi(puls_plan()), write_mspdi(puls_plan())
    assert first == second
    root = ET.fromstring(first)
    uids = [t.findtext(Q + "UID") for t in root.iter(Q + "Task")]
    assert uids == ["0", "1", "2", "3", "4", "5"]
    guids = [t.findtext(Q + "GUID") for t in root.iter(Q + "Task")]
    assert len(set(guids)) == 6 and all(len(g) == 36 for g in guids)
    assert "Удалённая" not in first.decode()


def test_экспорт_повтор_guid_и_недопустимые_символы(doc):
    plan = deepcopy(doc)
    copy = deepcopy(row(plan, 42))
    copy.update({"key": "copy42", "name": "Закрытие\x0bпроекта\x01", "position": 99999})
    copy["extra"]["msp"]["uid"] = 42
    plan["items"].append(copy)
    root = written(plan)  # без недопустимых символов файл бы не разобрался
    guids = [t.findtext(Q + "GUID") for t in root.iter(Q + "Task")]
    assert len(guids) == len(set(guids))
    names = [t.findtext(Q + "Name") for t in root.iter(Q + "Task")]
    assert "Закрытиепроекта" in names
    assert xml_task(root, 45).findtext(Q + "Name") == "Закрытиепроекта"


def test_экспорт_цикл_родителей_не_зацикливается():
    plan = {"items": [
        {"key": "a", "type": "group", "name": "А", "parentKey": "b", "position": 1},
        {"key": "b", "type": "group", "name": "Б", "parentKey": "a", "position": 2},
        {"key": "c", "type": "task", "name": "В", "parentKey": "b", "position": 3, "start": "2026-03-02",
         "end": "2026-03-03"},
    ]}
    root = written(plan)
    assert [(t.findtext(Q + "Name"), t.findtext(Q + "OutlineNumber")) for t in root.iter(Q + "Task")][1:] == [
        ("А", "1"), ("Б", "1.1"), ("В", "1.1.1")]


def test_экспорт_uid_новых_строк_после_занятых(doc):
    grown = deepcopy(doc)
    grown["items"].append({"key": "new1", "type": "task", "name": "Новая", "parentKey": None, "position": 99999,
                           "start": "2026-07-30", "end": "2026-07-31"})
    assert xml_task(written(grown), 45).findtext(Q + "Name") == "Новая"


def test_экспорт_задача_с_подзадачами_суммарная():
    root = written(puls_plan())
    development = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Разработка")
    assert development.findtext(Q + "Summary") == "1" and development.findtext(Q + "OutlineLevel") == "2"
    build = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Сборка")
    assert build.findtext(Q + "OutlineLevel") == "3" and build.findtext(Q + "OutlineNumber") == "1.1.1"


def test_экспорт_связей_с_лагами():
    root = written(puls_plan())
    testing = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Тестирование")
    fs = testing.find(Q + "PredecessorLink")
    assert (fs.findtext(Q + "Type"), fs.findtext(Q + "LinkLag"), fs.findtext(Q + "LagFormat")) == ("1", "9600", "7")
    release = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Релиз")
    ss = release.find(Q + "PredecessorLink")
    assert (ss.findtext(Q + "Type"), ss.findtext(Q + "LinkLag"), ss.findtext(Q + "LagFormat")) == ("3", "43200", "8")
    assert ss.findtext(Q + "PredecessorUID") == testing.findtext(Q + "UID")


def test_экспорт_плана_до_0_7_0_со_связью_в_строке():
    plan = puls_plan()
    del plan["links"]
    plan["items"][2].update({"dependencyKey": "t1", "dependencyType": "FF", "dependencyLag": 1})
    testing = next(t for t in written(plan).iter(Q + "Task") if t.findtext(Q + "Name") == "Тестирование")
    link_xml = testing.find(Q + "PredecessorLink")
    assert (link_xml.findtext(Q + "Type"), link_xml.findtext(Q + "LinkLag"), link_xml.findtext(Q + "LagFormat")) == (
        "0", "14400", "8")


def test_экспорт_исполнителей_и_назначений():
    root = written(puls_plan(), users=[{"id": 1, "email": "ivanov@romashka.ru"}])
    resources = root.findall(f"{Q}Resources/{Q}Resource")
    assert [(r.findtext(Q + "Name"), r.findtext(Q + "Type"), r.findtext(Q + "ID")) for r in resources] == [
        ("Иванов Иван", "1", "1"), ("Петрова Анна", "1", "2")]
    assert resources[0].findtext(Q + "EmailAddress") == "ivanov@romashka.ru"
    assert resources[1].findtext(Q + "Group") == "QA"
    assignments = root.findall(f"{Q}Assignments/{Q}Assignment")
    assert len(assignments) == 1  # у задачи с подзадачами (суммарной) назначения нет
    testing = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Тестирование")
    assert assignments[0].findtext(Q + "TaskUID") == testing.findtext(Q + "UID")
    assert assignments[0].findtext(Q + "ResourceUID") == resources[1].findtext(Q + "UID")
    assert assignments[0].findtext(Q + "Work") == "PT24H0M0S"


def test_экспорт_крайних_сроков_и_ограничений():
    root = written(puls_plan())
    testing = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Тестирование")
    assert testing.findtext(Q + "Deadline") == "2026-03-13T17:00:00"
    assert [testing.findtext(Q + n) for n in ("ConstraintType", "ConstraintDate")] == ["4", "2026-03-10T08:00:00"]
    release = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Релиз")
    assert [release.findtext(Q + n) for n in ("ConstraintType", "ConstraintDate")] == ["3", "2026-03-16T17:00:00"]


def test_экспорт_прогресса_и_факта():
    root = written(puls_plan())
    testing = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Тестирование")
    assert testing.findtext(Q + "PercentComplete") == "100"
    assert testing.findtext(Q + "ActualStart") == "2026-03-10T08:00:00"
    assert testing.findtext(Q + "ActualFinish") == "2026-03-13T17:00:00"
    build = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Сборка")
    assert build.findtext(Q + "PercentComplete") == "0" and build.find(Q + "ActualStart") is None


def test_экспорт_комментариев_в_заметки():
    testing = next(t for t in written(puls_plan()).iter(Q + "Task") if t.findtext(Q + "Name") == "Тестирование")
    assert testing.findtext(Q + "Notes") == "Регресс по чек-листу\n\nиз файла"
    quiet = next(t for t in written(puls_plan(), include_comments=False).iter(Q + "Task")
                 if t.findtext(Q + "Name") == "Тестирование")
    assert quiet.find(Q + "Notes") is None


def test_экспорт_категории_и_ссылки_jira():
    root = written(puls_plan())
    definition = root.find(f"{Q}ExtendedAttributes/{Q}ExtendedAttribute")
    assert (definition.findtext(Q + "FieldID"), definition.findtext(Q + "Alias")) == ("188743731", "Категория")
    development = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Разработка")
    assert development.find(Q + "ExtendedAttribute") is None  # у суммарной категории нет
    plan = puls_plan()
    plan["items"][3]["category"] = "сборка"
    plan["items"][3]["jiraUrl"] = "https://jira.romashka.ru/browse/REL-8"
    build = next(t for t in written(plan).iter(Q + "Task") if t.findtext(Q + "Name") == "Сборка")
    assert build.findtext(f"{Q}ExtendedAttribute/{Q}Value") == "сборка"
    assert (build.findtext(Q + "Hyperlink"), build.findtext(Q + "HyperlinkAddress")) == (
        "REL-8", "https://jira.romashka.ru/browse/REL-8")


def test_экспорт_календаря_с_исключениями():
    holidays = {"2026-01-01": "Новогодние каникулы", "2026-01-02": "Новогодние каникулы", "2026-03-09": "Перенос"}
    root = written(puls_plan(), holidays=holidays, workdays=["2026-03-14"])
    calendar = root.find(f"{Q}Calendars/{Q}Calendar")
    assert calendar.findtext(Q + "Name") == "Стандартный" and root.findtext(Q + "CalendarUID") == "1"
    days = {d.findtext(Q + "DayType"): d.findtext(Q + "DayWorking") for d in calendar.iter(Q + "WeekDay")}
    assert days == {"1": "0", "2": "1", "3": "1", "4": "1", "5": "1", "6": "1", "7": "0"}
    exceptions = calendar.findall(f"{Q}Exceptions/{Q}Exception")
    assert [(e.findtext(Q + "Name"), e.findtext(f"{Q}TimePeriod/{Q}FromDate"), e.findtext(f"{Q}TimePeriod/{Q}ToDate"),
             e.findtext(Q + "DayWorking")) for e in exceptions] == [
        ("Новогодние каникулы", "2026-01-01T00:00:00", "2026-01-02T23:59:00", "0"),
        ("Перенос", "2026-03-09T00:00:00", "2026-03-09T23:59:00", "0"),
        ("Рабочий день", "2026-03-14T00:00:00", "2026-03-14T23:59:00", "1")]
    assert len(exceptions[2].findall(f"{Q}WorkingTimes/{Q}WorkingTime")) == 2
    # Длительность задачи — по рабочим дням календаря: 09.03 — праздник
    plan = puls_plan()
    del plan["items"][3]
    plan["items"][1]["offset"] = 0
    plan["items"][1].update({"start": "2026-03-06", "end": "2026-03-10"})
    development = next(t for t in written(plan, holidays=holidays).iter(Q + "Task")
                       if t.findtext(Q + "Name") == "Разработка")
    assert development.findtext(Q + "Duration") == "PT16H0M0S"


def test_экспорт_сдвига_в_базовый_план():
    plan = puls_plan()
    del plan["items"][3]  # «Разработка» без подзадач: обычная задача со сдвигом 2 дня
    development = next(t for t in written(plan).iter(Q + "Task") if t.findtext(Q + "Name") == "Разработка")
    assert development.findtext(Q + "Finish") == "2026-03-08T17:00:00"
    baseline = development.find(Q + "Baseline")
    assert (baseline.findtext(Q + "Number"), baseline.findtext(Q + "Start"), baseline.findtext(Q + "Finish")) == (
        "0", "2026-03-02T08:00:00", "2026-03-06T17:00:00")


def test_экспорт_базового_плана_из_параметра(doc):
    root = written(doc, baseline=doc["baseline"]["items"])
    interview = xml_task(root, 6).find(Q + "Baseline")
    assert (interview.findtext(Q + "Start"), interview.findtext(Q + "Finish")) == (
        "2026-03-10T08:00:00", "2026-03-19T17:00:00")
    assert xml_task(root, 16).find(Q + "Baseline") is None


def test_экспорт_автопланирование_закрепляет_даты():
    root = written(puls_plan(), scheduling="auto")
    build = next(t for t in root.iter(Q + "Task") if t.findtext(Q + "Name") == "Сборка")
    assert (build.findtext(Q + "Manual"), build.findtext(Q + "ConstraintType")) == ("0", "4")
    assert build.findtext(Q + "ConstraintDate") == "2026-03-04T08:00:00" and build.find(Q + "ManualStart") is None
    assert root.findtext(Q + "NewTasksAreManual") == "0"
    manual = written(puls_plan())
    build = next(t for t in manual.iter(Q + "Task") if t.findtext(Q + "Name") == "Сборка")
    assert build.findtext(Q + "Manual") == "1" and build.findtext(Q + "ManualStart") == "2026-03-04T08:00:00"


# ================================================================ круговой обход
def without_outline_numbers(items):
    """Номер структуры пересчитывается при экспорте (пропущенные строки дают пропуски в исходных номерах)."""
    out = deepcopy(items)
    for item in out:
        item["extra"]["msp"].pop("outlineNumber")
    return out


@pytest.mark.parametrize("options", [
    {},
    {"durations": "working"},
    {"baseline": "baseline"},
], ids=["даты", "рабочие дни", "базовый план"])
def test_круговой_обход_импорт_экспорт_импорт(crm, options):
    first = map_project(crm, {**OPTIONS, **options})
    data = write_mspdi(first, users=USERS, holidays=["2026-03-09", "2026-05-01", "2026-05-11", "2026-06-12"])
    second = map_project(read_mspdi(data), {**OPTIONS, **options})
    assert without_outline_numbers(second["items"]) == without_outline_numbers(first["items"])
    if options.get("baseline"):
        # Строки после сдвинутых предшественников не согласованы с текущими сроками файла (это отставание и
        # показывает расчёт), а выгрузка пишет текущие сроки «Пульса» — лаги таких связей подбираются заново.
        # Остальное — как было, а повторный обход уже ничего не меняет
        def shape(links):
            return [{k: v for k, v in x.items() if k != "lag"} for x in links]
        assert shape(second["links"]) == shape(first["links"])
        third = map_project(read_mspdi(write_mspdi(second, users=USERS, holidays=["2026-03-09", "2026-05-01",
                                                                                   "2026-05-11", "2026-06-12"])),
                            {**OPTIONS, **options})
        assert third["links"] == second["links"]
    else:
        assert second["links"] == first["links"]
    assert second["assignees"] == first["assignees"]
    assert second["comments"] == first["comments"]
    assert second["settings"]["categories"] == first["settings"]["categories"]
    assert [i["extra"]["msp"]["outlineNumber"] for i in second["items"]][:4] == ["1", "1.1", "1.2", "1.3"]
    assert diff_plan(as_plan(first), second).counts["changed"] == 0


def test_круговой_обход_плана_пульса():
    plan = puls_plan()
    del plan["items"][3]
    document = map_project(read_mspdi(write_mspdi(plan, users=[{"id": 1, "email": "ivanov@romashka.ru"}])),
                           {"categoryField": "188743731", "jiraBaseUrls": [JIRA], "baseline": "baseline",
                            "users": [{"id": 1, "name": "Иванов Иван", "email": "ivanov@romashka.ru"},
                                      {"id": 2, "name": "Петрова Анна", "email": ""}]})
    by_name = {i["name"]: i for i in document["items"]}
    development = by_name["Разработка"]
    assert (development["start"], development["end"], development["offset"]) == ("2026-03-02", "2026-03-06", 2)
    assert (development["category"], development["jiraUrl"]) == ("разработка", "https://jira.romashka.ru/browse/REL-7")
    assert by_name["Релиз"]["date"] == "2026-03-16" and by_name["Релиз 2.0"]["type"] == "group"
    assert by_name["Тестирование"]["completedDate"] == "2026-03-13"
    # План-образец не согласован со своими связями (FS +2 раб. дн. от окончания со сдвигом 08.03 — это 12.03, а
    # «Тестирование» начинается 10.03): лаги подбираются по текущим срокам файла
    assert [(x["type"], x["lag"], x["lagKind"]) for x in document["links"]] == [
        ("FS", 0, "working"), ("SS", 6, "calendar")]
    assert [(a["key"], a["name"], a["userId"]) for a in document["assignees"]] == [
        ("a1", "Иванов Иван", 1), ("a2", "Петрова Анна", 2)]


# ================================================================ согласование со связями «Пульса» (даты файла — главные)
VITEST_FIXTURES = Path(__file__).resolve().parents[2] / "src" / "gantt" / "domain" / "__tests__" / "fixtures"
SCHEDULE_FIELDS = ("key", "type", "name", "parentKey", "start", "end", "date", "offset", "status", "completedDate",
                   "constraintType", "constraintDate")


def puls_calendar(document):
    return RuCalendar(document["pulsCalendar"]["holidays"], document["pulsCalendar"]["workdays"])


def varied_file(phases=24, per_phase=25, seed=7):
    """MSPDI со случайными (но повторяемыми) сроками и связями: все типы, лаги в минутах, часах, днях, неделях,
    прошедших единицах и процентах, отрицательные лаги, связи суммарных задач, вехи в 08:00 и 17:00, окончания в
    08:00, завершённые задачи с фактом раньше окончания, «Начало не ранее», даты на выходных и праздниках."""
    rnd = random.Random(seed)
    formats = [7, 7, 7, 5, 3, 8, 6, 9, 10, 19, 20, 39, 40]
    minutes = [0, 0, 240, 480, 960, 2400, -480, -240, 720, 2160]
    parts = [f'<?xml version="1.0" encoding="UTF-8"?><Project xmlns="{NS}"><Name>Случайный.xml</Name>'
             "<MinutesPerDay>480</MinutesPerDay><Tasks>"
             "<Task><UID>0</UID><ID>0</ID><Name>Случайный</Name><OutlineLevel>0</OutlineLevel><Summary>1</Summary></Task>"]
    uid, leaves, phases_done = 0, [], []
    origin = date(2026, 1, 12)
    for phase in range(phases):
        uid += 1
        phase_uid = uid
        parts.append(f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>Фаза {phase + 1}</Name><OutlineLevel>1</OutlineLevel>"
                     "<Start>2026-01-12T08:00:00</Start><Finish>2026-12-30T17:00:00</Finish><Summary>1</Summary></Task>")
        for n in range(per_phase):
            uid += 1
            start = origin + timedelta(days=rnd.randint(0, 330))
            body = []
            if rnd.random() < 0.1:
                at = rnd.choice(["08:00:00", "17:00:00"])
                body.append(f"<Start>{start}T{at}</Start><Finish>{start}T{at}</Finish><Duration>PT0H0M0S</Duration>"
                            "<Milestone>1</Milestone>")
            else:
                days = rnd.randint(1, 15)
                finish = f"{start + timedelta(days=days)}T08:00:00" if rnd.random() < 0.1 else \
                    f"{start + timedelta(days=days - 1)}T17:00:00"
                body.append(f"<Start>{start}T{rnd.choice(['08:00:00', '13:00:00'])}</Start><Finish>{finish}</Finish>"
                            f"<Duration>PT{days * 8}H0M0S</Duration>")
                if rnd.random() < 0.15:
                    actual = start + timedelta(days=max(0, days - 1 - rnd.randint(0, 2)))
                    body.append(f"<PercentComplete>100</PercentComplete><ActualFinish>{actual}T17:00:00</ActualFinish>")
            if rnd.random() < 0.08:
                snet = start + timedelta(days=rnd.randint(-3, 3))
                body.append(f"<ConstraintType>4</ConstraintType><ConstraintDate>{snet}T08:00:00</ConstraintDate>")
            for _ in range(rnd.choice([0, 1, 1, 2, 3]) if leaves else 0):
                source = rnd.choice(phases_done) if phases_done and rnd.random() < 0.05 else rnd.choice(leaves)
                fmt = rnd.choice(formats)
                lag = rnd.choice([0, 25, 50, 100]) if fmt in (19, 20) else rnd.choice(minutes) * 10
                body.append(pred(source, kind=rnd.randint(0, 3), lag=lag, fmt=fmt))
            parts.append(f"<Task><UID>{uid}</UID><ID>{uid}</ID><Name>Работа {phase + 1}.{n + 1}</Name>"
                         f"<OutlineLevel>2</OutlineLevel>{''.join(body)}</Task>")
            leaves.append(uid)
        phases_done.append(phase_uid)
    parts.append("</Tasks></Project>")
    return "".join(parts).encode()


def vitest_fixture(name, document):
    """Строки и связи документа загрузки — для msprojectConsistency.test.js (тот же расчёт на клиенте)."""
    items = [{k: item[k] for k in SCHEDULE_FIELDS if item.get(k) not in (None, "")} for item in document["items"]]
    lines = ['{', f' "name": {json.dumps(name, ensure_ascii=False)},',
             f' "calendar": {json.dumps(document["pulsCalendar"], ensure_ascii=False, sort_keys=True)},',
             ' "items": [']
    lines += [f"  {json.dumps(item, ensure_ascii=False, sort_keys=True)}," for item in items]
    lines[-1] = lines[-1].rstrip(",")
    lines += [' ],', ' "links": [']
    lines += [f"  {json.dumps(link, ensure_ascii=False, sort_keys=True)}," for link in document["links"]]
    lines[-1] = lines[-1].rstrip(",")
    lines += [' ]', '}', '']
    return "\n".join(lines)


@pytest.mark.parametrize("case", ["crm", "crm-working", "generated-baseline", "6day", "generated"])
def test_план_согласован_со_связями_пульса(case):
    """Автосдвиг «Пульса» не трогает ни одной строки плана из файла; документы — для теста vitest на клиенте.
    generated-baseline — «по базовому плану» без отставания (в файле нет базового плана — текущие сроки): до 0.7.1
    лаги там не подбирались, и расчёт двигал строки из-за перевода лагов в дни."""
    source = {"crm": CRM.read_bytes(), "crm-working": CRM.read_bytes(), "6day": SIX_DAY.read_bytes(),
              "generated": None, "generated-baseline": None}[case]
    options = {"crm-working": {"durations": "working"}, "generated-baseline": {"baseline": "baseline"}}.get(case, {})
    document = map_project(read_mspdi(source or varied_file()), options)
    assert inconsistent_rows(document["items"], document["links"], puls_calendar(document)) == []
    assert document["links"]
    path = VITEST_FIXTURES / f"msproject-{case}.json"
    text = vitest_fixture(case, document)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["links"] == document["links"]


def test_без_подбора_лагов_план_был_бы_не_согласован(doc):
    """Проверка самой проверки: лаги «как в файле» (4ч → 1 день, SF 0) автосдвиг передвинул бы."""
    links = deepcopy(doc["links"])
    link_by_key = {x["key"]: x for x in links}
    link_by_key["l11-12"]["lag"] = 1
    link_by_key["l20-21"]["lag"] = 0
    assert inconsistent_rows(doc["items"], links, puls_calendar(doc)) == ["i12", "i21"]


ALIGN_CASES = {
    # SF: окончание — к началу предшественника, то есть в конце предыдущего рабочего дня
    "SF": (task(1, "А", "2026-03-02T08:00:00", "2026-03-06T17:00:00")
           + task(2, "Б", "2026-02-25T08:00:00", "2026-02-27T17:00:00", body=pred(1, kind=2)),
           [(-1, "working")], False),
    # +4ч после окончания в пятницу 17:00 — понедельник
    "FS +4ч": (task(1, "А", "2026-03-10T08:00:00", "2026-03-13T17:00:00")
               + task(2, "Б", "2026-03-16T13:00:00", "2026-03-17T17:00:00", body=pred(1, lag=2400, fmt=5)),
               [(0, "working")], False),
    # +36еч после четверга 17:00 — суббота 05:00, MS Project начинает в понедельник
    "FS +36еч на выходной": (task(1, "А", "2026-03-10T08:00:00", "2026-03-12T17:00:00")
                             + task(2, "Б", "2026-03-16T08:00:00", "2026-03-17T17:00:00",
                                    body=pred(1, lag=21600, fmt=6)),
                             [(3, "calendar")], True),
    # Задача позже, чем требует связь (выравнивание, ручная дата): связь становится определяющей
    "резерв": (task(1, "А", "2026-03-10T08:00:00", "2026-03-13T17:00:00")
               + task(2, "Б", "2026-03-18T08:00:00", "2026-03-19T17:00:00", body=pred(1)),
               [(2, "working")], True),
    # Задача раньше, чем требует связь: лаг — до даты файла
    "опережение": (task(1, "А", "2026-03-10T08:00:00", "2026-03-13T17:00:00")
                   + task(2, "Б", "2026-03-11T08:00:00", "2026-03-12T17:00:00", body=pred(1)),
                   [(-3, "working")], True),
    # Два предшественника: определяющий — поздний, второй остаётся неопределяющим
    "два предшественника": (task(1, "А", "2026-03-10T08:00:00", "2026-03-13T17:00:00")
                            + task(2, "В", "2026-03-02T08:00:00", "2026-03-04T17:00:00")
                            + task(3, "Б", "2026-03-16T08:00:00", "2026-03-17T17:00:00", body=pred(1) + pred(2)),
                            [(0, "working"), (0, "working")], False),
    # Вехи: FS — в день окончания, SS — в день начала
    "вехи": (task(1, "А", "2026-03-10T08:00:00", "2026-03-13T17:00:00")
             + task(2, "Конец", "2026-03-13T17:00:00", "2026-03-13T17:00:00", duration="PT0H0M0S", body=pred(1))
             + task(3, "Начало", "2026-03-10T08:00:00", "2026-03-10T08:00:00", duration="PT0H0M0S",
                    body=pred(1, kind=3)),
             [(0, "working"), (0, "working")], False),
    # Завершённая задача: связь считается от даты завершения (факта)
    "факт": (task(1, "А", "2026-03-10T08:00:00", "2026-03-13T17:00:00",
                  body="<PercentComplete>100</PercentComplete><ActualFinish>2026-03-12T17:00:00</ActualFinish>")
             + task(2, "Б", "2026-03-16T08:00:00", "2026-03-17T17:00:00", body=pred(1)),
             [(1, "working")], True),
    # «Начало не ранее» держит задачу сам — связь не трогается
    "начало не ранее": (task(1, "А", "2026-03-10T08:00:00", "2026-03-11T17:00:00")
                        + task(2, "Б", "2026-03-16T08:00:00", "2026-03-17T17:00:00",
                               body=pred(1) + "<ConstraintType>4</ConstraintType>"
                                              "<ConstraintDate>2026-03-16T08:00:00</ConstraintDate>"),
                        [(0, "working")], False),
    # Предшественник кончается накануне праздника: следующий рабочий день — после праздника (09.03.2026)
    "праздник": (task(1, "А", "2026-03-02T08:00:00", "2026-03-06T17:00:00")
                 + task(2, "Б", "2026-03-10T08:00:00", "2026-03-11T17:00:00", body=pred(1)),
                 [(0, "working")], False),
}


@pytest.mark.parametrize("case", list(ALIGN_CASES))
def test_лаги_подобраны_под_даты_файла(case):
    rows, lags, adjusted = ALIGN_CASES[case]
    document = mapped(mspdi(rows))
    assert [(x["lag"], x["lagKind"]) for x in document["links"]] == lags
    assert (warning(document, "lags_adjusted") is not None) == adjusted
    assert inconsistent_rows(document["items"], document["links"], puls_calendar(document)) == []


def test_по_базовому_плану_лаги_по_текущим_срокам(crm):
    """По базовому плану строки — плановые сроки и сдвиг, а лаги — те же, что при загрузке по текущим срокам: их
    MS Project рассчитал по этим связям. Несогласованы со связями только строки, которые и в MS Project начинаются
    позже базового плана, — отставание, которое и должен показать расчёт (0.7.1; раньше лаги не подбирались, и
    погрешность перевода лагов в дни тоже выглядела отставанием)."""
    base = map_project(crm, {"baseline": "baseline"})
    current = map_project(crm, {})
    assert base["links"] == current["links"] and warning(base, "lags_adjusted") is not None
    starts = {i["key"]: i.get("start") or i.get("date") for i in current["items"]}
    late = {i["key"] for i in base["items"] if (i.get("start") or i.get("date")) and starts[i["key"]] > (i.get("start") or i.get("date"))}
    flagged = inconsistent_rows(base["items"], base["links"], puls_calendar(base))
    assert flagged and set(flagged) <= late


def test_календарь_пульса_из_параметров():
    rows = task(1, "А", "2026-03-10T08:00:00", "2026-03-13T17:00:00") + task(
        2, "Б", "2026-03-17T08:00:00", "2026-03-18T17:00:00", body=pred(1))
    document = mapped(mspdi(rows), calendar={"holidays": ["2026-03-16"], "workdays": []})
    assert document["pulsCalendar"] == {"holidays": ["2026-03-16"], "workdays": []}
    assert [(x["lag"], x["lagKind"]) for x in document["links"]] == [(0, "working")]


# ================================================================ замечания проверки 0.7.0
def test_review_doctype_в_utf16_без_метки_после_пробела():
    """MEDIUM 4: expat считает UTF-16 любой файл с нулём во втором байте — пробел перед DOCTYPE прятал его."""
    doc = ('<!DOCTYPE Project [<!ENTITY a "AAAAAAAAAA"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
           f'<Project xmlns="{NS}"><Name>&b;</Name><Tasks><Task><UID>1</UID><Name>&b;</Name>'
           "<OutlineLevel>1</OutlineLevel><Start>2026-03-02T08:00:00</Start><Finish>2026-03-02T17:00:00</Finish>"
           "</Task></Tasks></Project>")
    for data in ((" " + doc).encode("utf-16-le"), ("\n" + doc).encode("utf-16-be"),
                 ("\t\n " + doc).encode("utf-16-le"), b"\xff\xfe" + (" " + doc).encode("utf-16-le")):
        with pytest.raises(MsProjectError, match="DOCTYPE"):
            read_mspdi(data)


def test_review_исключения_календаря_на_тысячелетия():
    """MEDIUM 5: еженедельное исключение 1900–9000 с пустой маской перебиралось день за днём (2 с на штуку),
    «по 31.12.9999» давало OverflowError."""
    exception = ("<Exception><TimePeriod><FromDate>1900-01-01T00:00:00</FromDate><ToDate>{to}</ToDate></TimePeriod>"
                 "<Name>x</Name><Type>{kind}</Type><Period>1</Period><DaysOfWeek>{mask}</DaysOfWeek>"
                 "<DayWorking>0</DayWorking></Exception>")
    exceptions = "".join(exception.format(to="9000-01-01T00:00:00", kind=6, mask=0) for _ in range(10))
    exceptions += exception.format(to="9999-12-31T23:59:59", kind=1, mask=0)
    exceptions += exception.format(to="9999-12-31T23:59:59", kind=6, mask=2)
    calendars = (f"<Calendar><UID>1</UID><Name>С</Name><IsBaseCalendar>1</IsBaseCalendar>"
                 f"<Exceptions>{exceptions}</Exceptions></Calendar>")
    data = mspdi(task(1, "А"), calendars=calendars, head="<CalendarUID>1</CalendarUID>")
    started = time.perf_counter()
    document = mapped(data)
    assert time.perf_counter() - started < 1
    days = document["calendar"]["exceptions"]
    assert days and min(d["date"] for d in days) >= "2025-01-01" and max(d["date"] for d in days) <= "2027-12-31"


def test_review_даты_вне_диапазона_ms_project():
    """MEDIUM 7: 0001-01-01 и 9999-12-31 давали OverflowError (500) — теперь это пустые даты."""
    rows = (task(1, "Год один", start="0001-01-01T00:00:00") + task(2, "Год 9999", finish="9999-12-31T17:00:00")
            + task(3, "Обычная", body="<Deadline>9999-12-31T17:00:00</Deadline>"))
    document = mapped(mspdi(rows))
    assert [i["name"] for i in document["items"]] == ["Обычная"] and document["items"][0]["deadline"] is None
    assert warning(document, "invalid_dates")["examples"] == ["Год один", "Год 9999"]


def test_review_rtf_вне_юникода_nul_и_суррогаты():
    """MEDIUM 7: \\u1114112 ронял разбор (500), \\'00, \\u0 и суррогаты попадали в комментарий (PostgreSQL)."""
    assert plain_text("{\\rtf1 a \\u1114112 b}") == "a"
    assert plain_text("{\\rtf1 a\\'00b\\u0?c}") == "abc"   # после \uN — один замещающий символ («?»)
    assert plain_text("{\\rtf1 x\\u55296 y}") == "x"
    assert plain_text("<p>a&#0;b</p>") == "a\ufffdb"
    rows = task(1, "А", body="<Notes>{\\rtf1 до\\'00после \\u55296?}</Notes>")
    text = mapped(mspdi(rows))["comments"][0]["text"]
    assert "\x00" not in text and not any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def test_review_ограничение_без_даты():
    """MEDIUM 7: «Начало не ранее» без даты проходило проверку, а применение давало 400."""
    document = mapped(mspdi(task(1, "А", body="<ConstraintType>4</ConstraintType>")
                            + task(2, "Б", body="<ConstraintType>1</ConstraintType>")))
    assert [(i["constraintType"], i["constraintDate"]) for i in document["items"]] == [(None, None), ("alap", None)]
    assert warning(document, "constraints_without_date")["examples"] == ["А"]


def test_review_шкала_не_длиннее_15_лет():
    """MEDIUM 7: проект на 20 лет давал шкалу, которую сервер отклонял («Шкала не может быть длиннее 15 лет»)."""
    document = mapped(mspdi(task(1, "А", "2010-01-01T08:00:00", "2031-01-01T17:00:00")
                            + task(2, "Б", "2026-03-02T08:00:00", "2026-03-02T17:00:00")))
    plan_range = document["settings"]["range"]
    assert (date.fromisoformat(plan_range["end"]) - date.fromisoformat(plan_range["start"])).days <= 366 * 15
    assert plan_range["start"] == "2009-12-25" and warning(document, "range_too_long")


def test_review_ошибки_значений_не_роняют_сервер(crm, monkeypatch):
    """MEDIUM 7: любая ошибка арифметики дат при сопоставлении — MsProjectError (400), а не 500."""
    from app.services.msproject import mapping

    def broken(*_args, **_kwargs):
        raise OverflowError("date value out of range")

    monkeypatch.setattr(mapping, "_range", broken)
    with pytest.raises(MsProjectError, match="недопустимые значения"):
        map_project(crm)


def test_review_uid_ресурса_не_из_ключа_пульса():
    """LOW 14: ключ исполнителя «Пульса» из одних цифр (a1234567890) давал UID больше 32 бит."""
    plan = puls_plan()
    plan["assignees"] = [{"key": "a1234567890", "name": "Иванов", "position": 1},
                         {"key": "a3", "name": "Петров", "position": 2},
                         {"key": "a9999999999", "name": "Сидоров", "position": 3}]
    plan["items"][0]["extra"] = {"msp": {"uid": 9_999_999_999}}
    root = written(plan)
    assert [int(r.findtext(Q + "UID")) for r in root.iter(Q + "Resource")] == [4, 3, 5]
    assert max(int(t.findtext(Q + "UID")) for t in root.iter(Q + "Task")) < 10


def test_review_ровно_предельное_число_задач():
    """LOW 15: суммарная задача проекта (UID 0) входила в ограничение числа задач."""
    rows = ("<Task><UID>0</UID><ID>0</ID><Name>Проект</Name><OutlineLevel>0</OutlineLevel></Task>"
            + task(1, "А") + task(2, "Б") + task(3, "В"))
    assert len(read_mspdi(mspdi(rows), max_tasks=3).tasks) == 4
    with pytest.raises(MsProjectError, match="больше 2 задач"):
        read_mspdi(mspdi(rows), max_tasks=2)


def test_review_обновление_большого_плана_линейно():
    """LOW 18: позиции считались перебором всех строк для каждой группы (4,7 с на 2500 групп)."""
    document = map_project(read_mspdi(big_file(groups=2499, per_group=7)))
    plan = as_plan(document)
    started = time.perf_counter()
    changes = diff_plan(plan, document)
    assert time.perf_counter() - started < 2.5
    assert changes.counts["changed"] == 0 and changes.counts["unchanged"] == len(document["items"])


class FakePlan:
    """План «Пульса» для сценариев обновления: строки, связи, исполнители и корзина; создание с занятым ключом
    (в том числе удалённым) — ошибка, как на сервере (409 key_exists или пропуск повтора)."""

    def __init__(self, document):
        self.stores = {"item": {}, "link": {}, "assignee": {}}
        self.trash = {"item": {}, "link": {}, "assignee": {}}
        self.settings = deepcopy(document["settings"])
        self.apply([{"op": "assignee.create", "assignee": a} for a in document["assignees"]]
                   + [{"op": "item.create", "item": i} for i in document["items"]]
                   + [{"op": "link.create", "link": x} for x in document["links"]])

    def keys(self):
        return {key for kind in self.stores for key in [*self.stores[kind], *self.trash[kind]]}

    def document(self):
        return {"items": sorted(deepcopy(list(self.stores["item"].values())), key=lambda i: i["position"]),
                "links": deepcopy(list(self.stores["link"].values())),
                "assignees": deepcopy(list(self.stores["assignee"].values())), "settings": self.settings}

    def apply(self, ops):
        for op in ops:
            kind, action = op["op"].split(".")
            store = self.stores[kind]
            if action == "create":
                payload = op[kind]
                assert payload["key"] not in self.keys(), f"key_exists: {payload['key']}"
                store[payload["key"]] = deepcopy(payload)
            elif action == "update":
                store[op["key"]].update(deepcopy(op["fields"]))
            elif action == "delete":
                self.trash[kind][op["key"]] = store.pop(op["key"])
                if kind == "item":
                    for key, value in list(self.stores["link"].items()):
                        if op["key"] in (value["from"], value["to"]):
                            self.trash["link"][key] = self.stores["link"].pop(key)

    def update(self, document, **options):
        changes = diff_plan(self.document(), document, reserved_keys=self.keys())
        self.apply(to_ops(changes, options))
        return changes


def test_review_строка_удалена_в_пульсе_есть_в_файле(doc):
    """Строка, удалённая в «Пульсе», но оставшаяся в файле, возвращается новой строкой со свежим ключом, и это
    видно в плане изменений; ключ из корзины не занимается (сервер отклонил бы создание или пропустил его)."""
    plan = FakePlan(doc)
    plan.apply([{"op": "item.delete", "key": "i29"}])
    changes = plan.update(doc)
    entry = changes.new[0]
    assert (entry["key"], entry["deletedInPuls"], entry["deletedKey"]) == ("i29x2", True, "i29")
    assert changes.counts["deletedInPuls"] == 1 and changes.warnings[0]["code"] == "deleted_in_puls"
    assert [x["key"] for x in changes.links["added"]] == ["l28-29x2"]
    assert next(i for i in plan.document()["items"] if i["key"] == "i29x2")["name"] == "Нагрузочное тестирование"
    again = diff_plan(plan.document(), doc, reserved_keys=plan.keys())
    assert to_ops(again) == [] and again.counts["new"] == 0


def test_review_строка_удалена_в_пульсе_известна_по_guid(doc):
    """Удалённая строка в документе плана (deleted: true) узнаётся по GUID, даже если ключ у неё другой."""
    plan = as_plan(doc)
    plan_row(plan, "k29")["deleted"] = True
    changes = diff_plan(plan, doc)
    assert [(c["key"], c["deletedKey"]) for c in changes.new] == [("i29", "k29")]


def test_review_связь_удалена_в_ms_project_и_добавлена_снова(doc):
    plan = FakePlan(doc)
    without = deepcopy(doc)
    without["links"] = [x for x in without["links"] if x["key"] != "l12-13"]
    removed = plan.update(without)
    assert [x["key"] for x in removed.links["removed"]] == ["l12-13"]
    readded = plan.update(doc)
    assert [x["key"] for x in readded.links["added"]] == ["l12-13x2"]
    assert to_ops(diff_plan(plan.document(), doc, reserved_keys=plan.keys())) == []


def test_review_задача_суммарная_задача(doc):
    """Задача стала суммарной, потом снова задачей: строка каждый раз создаётся заново со свежим ключом и не
    теряется (раньше второе создание попадало на ключ из корзины)."""
    plan = FakePlan(doc)
    grouped = deepcopy(doc)
    closing = row(grouped, 42)
    group = {k: closing[k] for k in ("key", "name", "parentKey", "position", "jiraUrl", "extra")}
    group.update({"type": "group", "color": "slate"})
    grouped["items"][grouped["items"].index(closing)] = group
    child = deepcopy(closing)
    child.update({"key": "i100", "name": "Подписание актов", "parentKey": "i42", "position": closing["position"] + 1,
                  "extra": {"msp": {"uid": 100, "guid": "00000000-0000-4000-8000-000000000100", "wbs": "7.1",
                                    "outlineNumber": "7.1"}}})
    grouped["items"].insert(grouped["items"].index(group) + 1, child)
    grouped["links"] = [{**x, "to": "i100", "key": "l41-100"} if x["key"] == "l41-42" else x for x in grouped["links"]]
    first = plan.update(grouped, deleteMissing=True)
    assert first.changed[0]["newKey"] == "i42x2" and [c["key"] for c in first.new] == ["i100"]
    back = plan.update(doc, deleteMissing=True)
    assert back.changed[0]["newKey"] == "i42x3" and [m["key"] for m in back.missing] == ["i100"]
    final = plan.document()
    closing_rows = [i for i in final["items"] if i["extra"]["msp"]["uid"] == 42]
    assert [(i["key"], i["type"]) for i in closing_rows] == [("i42x3", "task")]
    assert all(i["key"] != "i100" for i in final["items"])
    assert to_ops(diff_plan(final, doc, reserved_keys=plan.keys()), {"deleteMissing": True}) == []


def test_review_копия_строки_в_пульсе_не_удаляется(doc):
    """Копия строки, сделанная в «Пульсе» (с тем же extra.msp), — не «отсутствует в файле»: её задача в файле
    есть, и «удалить отсутствующие» её не трогает."""
    plan = FakePlan(doc)
    copy = deepcopy(row(doc, 8))
    copy.update({"key": "icopy", "name": "Отчёт об обследовании (копия)", "position": copy["position"] + 1})
    plan.apply([{"op": "item.create", "item": copy}])
    changes = diff_plan(plan.document(), doc, reserved_keys=plan.keys())
    assert changes.missing == [] and [c["key"] for c in changes.puls_only] == ["icopy"]
    assert changes.puls_only[0]["copyOf"]["uid"] == 8
    assert to_ops(changes, {"deleteMissing": True}) == []
