"""Схема плана: настройки по умолчанию, поля строк и их проверка.

Настройки плана (`plans.settings`) — общие для всех сотрудников параметры графика:
шкала дат, линия «сегодня», отображение (календарь, прогресс, пометки, легенда…),
справочник категорий. Масштаб, ширина колонки и свёрнутые группы — вид по умолчанию:
каждый сотрудник меняет их у себя (в браузере), не мешая коллегам.
"""

import math
import re
from copy import deepcopy
from datetime import date
from urllib.parse import urlparse

from app.security import ApiError

DEFAULT_CATEGORIES = [
    "согласование", "разработка", "разработка документации", "отработка замечаний", "тестирование",
    "подписание", "ожидание оплаты", "оплата", "процессы заказчика",
]

PALETTE = ("blue", "teal", "green", "orange", "red", "purple", "amber", "pink", "slate", "indigo", "cyan", "lime")
TOOLTIP_FIELDS = ("status", "dates", "progress", "dependency", "assignee", "lastChange")
# Что переносится из Jira в строки плана само — при открытии плана и обновлении из Jira (ADR-0007)
JIRA_AUTO_FIELDS = ("status", "dates", "progress", "name")

DEFAULT_SETTINGS = {
    "range": {"start": "2026-04-01", "end": "2026-12-31"},
    "today": {"auto": True, "date": None, "style": "dashed", "color": "red"},
    "scale": "day",
    "zoom": 1.0,
    "rowHeight": 44,
    "gridWidth": 280,
    "display": {
        "useRuCalendar": True,
        "showWeekends": True,
        "showHolidays": True,
        "showProgress": True,
        "showNotes": True,
        "autoShift": True,
        "legendEnabled": False,
        "legendPosition": "bottom",
        "legendMode": "all",
        "showTaskNamesOnBars": False,
        "showOffsetLabels": True,
        "showDependencies": True,
        "tooltipFields": list(TOOLTIP_FIELDS),
    },
    "categories": list(DEFAULT_CATEGORIES),
    "defaultCollapsed": [],
    "jiraAuto": [],
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_KEY = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def default_settings():
    return deepcopy(DEFAULT_SETTINGS)


def is_color(value):
    return isinstance(value, str) and (value in PALETTE or bool(_HEX.match(value)))


def parse_date(value, field):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ApiError(400, "validation_error", f"Неверная дата в поле «{field}»") from None


def check_key(value, what="ключ"):
    if not isinstance(value, str) or not _KEY.match(value):
        raise ApiError(400, "validation_error", f"Неверный {what}")
    return value


def safe_url(value):
    """Только http/https: ссылка javascript:… в 1.0.x выполнялась бы в окне системы."""
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ApiError(400, "validation_error", "Ссылка должна начинаться с http:// или https://")
    return value[:2000]


def _bool(value, default):
    return value if isinstance(value, bool) else default


def normalize_settings(raw, base=None):
    """Проверенные и дополненные значениями по умолчанию настройки плана.

    `raw` может содержать только часть ключей — они накладываются на `base`.
    """
    result = deepcopy(base) if base else default_settings()
    for key, value in DEFAULT_SETTINGS.items():
        result.setdefault(key, deepcopy(value))
    for key, value in DEFAULT_SETTINGS["display"].items():
        result["display"].setdefault(key, deepcopy(value))
    if not isinstance(raw, dict):
        return result

    if isinstance(raw.get("range"), dict):
        start = parse_date(raw["range"].get("start"), "Начало шкалы") or date.fromisoformat(result["range"]["start"])
        end = parse_date(raw["range"].get("end"), "Конец шкалы") or date.fromisoformat(result["range"]["end"])
        if end < start:
            raise ApiError(400, "validation_error", "Конец шкалы раньше начала")
        if (end - start).days > 366 * 15:
            raise ApiError(400, "validation_error", "Шкала не может быть длиннее 15 лет")
        result["range"] = {"start": start.isoformat(), "end": end.isoformat()}

    if isinstance(raw.get("today"), dict):
        today = dict(result["today"])
        t = raw["today"]
        if "auto" in t:
            today["auto"] = _bool(t["auto"], True)
        if "date" in t:
            parsed = parse_date(t["date"], "Дата «сегодня»")
            today["date"] = parsed.isoformat() if parsed else None
        if t.get("style") in ("dashed", "solid", "dotted"):
            today["style"] = t["style"]
        if "color" in t and is_color(t["color"]):
            today["color"] = t["color"]
        if not today["auto"] and not today["date"]:
            today["auto"] = True
        result["today"] = today

    if raw.get("scale") in ("day", "week", "month", "quarter"):
        result["scale"] = raw["scale"]
    if isinstance(raw.get("zoom"), (int, float)) and not isinstance(raw.get("zoom"), bool):
        result["zoom"] = min(6.0, max(0.05, float(raw["zoom"])))
    if isinstance(raw.get("rowHeight"), int) and not isinstance(raw.get("rowHeight"), bool):
        result["rowHeight"] = min(80, max(28, raw["rowHeight"]))
    if isinstance(raw.get("gridWidth"), int) and not isinstance(raw.get("gridWidth"), bool):
        result["gridWidth"] = min(700, max(160, raw["gridWidth"]))

    if isinstance(raw.get("display"), dict):
        display = dict(result["display"])
        for key, value in raw["display"].items():
            if key not in DEFAULT_SETTINGS["display"]:
                continue
            if key == "legendPosition" and value in ("top", "bottom"):
                display[key] = value
            elif key == "legendMode" and value in ("all", "main"):
                display[key] = value
            elif key == "tooltipFields" and isinstance(value, list):
                display[key] = [f for f in value if f in TOOLTIP_FIELDS]
            elif isinstance(DEFAULT_SETTINGS["display"][key], bool):
                display[key] = _bool(value, display[key])
        result["display"] = display

    if isinstance(raw.get("categories"), list):
        cats = []
        for c in raw["categories"]:
            if isinstance(c, str):
                c = " ".join(c.split())[:255]
                if c and c not in cats:
                    cats.append(c)
        result["categories"] = cats[:200]
    if isinstance(raw.get("defaultCollapsed"), list):
        result["defaultCollapsed"] = [k for k in raw["defaultCollapsed"] if isinstance(k, str) and _KEY.match(k)][:5000]
    if isinstance(raw.get("jiraAuto"), list):
        result["jiraAuto"] = [f for f in JIRA_AUTO_FIELDS if f in raw["jiraAuto"]]
    if isinstance(raw.get("legacy"), dict):
        result["legacy"] = raw["legacy"]
    return result


# Поля строки плана: имя в API -> (атрибут модели, тип)
ITEM_FIELDS = {
    "name": ("name", "text"),
    "parentKey": ("parent_key", "key"),
    "position": ("position", "float"),
    "status": ("status", "status"),
    "start": ("start_date", "date"),
    "end": ("end_date", "date"),
    "date": ("start_date", "date"),
    "endMode": ("end_mode", "end_mode"),
    "duration": ("duration", "int"),
    "durationType": ("duration_type", "duration_type"),
    "progress": ("progress", "progress"),
    "offset": ("offset_days", "int"),
    "completedDate": ("completed_date", "date"),
    "dependencyKey": ("dependency_key", "key"),
    "dependencyType": ("dependency_type", "dependency_type"),
    "dependencyLag": ("dependency_lag", "int"),
    "assigneeKey": ("assignee_key", "key"),
    "category": ("category", "short"),
    "color": ("color", "color"),
    "barText": ("bar_text", "short"),
    "jiraUrl": ("jira_url", "url"),
    "comment": ("comment", "text"),
    "lineStyle": ("line_style", "line_style"),
    "lineColor": ("line_color", "color"),
}

FIELD_LABELS = {
    "name": "Название", "parentKey": "Родитель", "position": "Порядок", "status": "Статус", "start": "Начало",
    "end": "Окончание", "date": "Дата", "endMode": "Режим завершения", "duration": "Длительность",
    "durationType": "Тип дней", "progress": "Прогресс", "offset": "Сдвиг", "completedDate": "Дата завершения",
    "dependencyKey": "Предшественник", "dependencyType": "Тип связи", "dependencyLag": "Лаг связи",
    "assigneeKey": "Исполнитель", "category": "Категория", "color": "Цвет", "barText": "Текст на полосе",
    "jiraUrl": "Ссылка Jira", "comment": "Комментарий", "lineStyle": "Стиль линии", "lineColor": "Цвет линии",
}

STATUS_LABELS = {"new": "Новая", "inwork": "В работе", "blocked": "Заблокирована", "done": "Завершена"}
TYPE_LABELS = {"task": "Задача", "milestone": "Веха", "group": "Группа"}


# Короткие текстовые поля строк — по размеру столбца
SHORT_MAX = {"category": 255, "barText": 500}


def convert_field(api_name, value):
    """Проверка и приведение значения поля строки к значению атрибута модели."""
    attr, kind = ITEM_FIELDS[api_name]
    label = FIELD_LABELS.get(api_name, api_name)
    if kind == "text":
        if value is None:
            return attr, None if api_name == "comment" else ""
        if not isinstance(value, str):
            raise ApiError(400, "validation_error", f"Поле «{label}» должно быть строкой")
        if len(value) > 5000:
            raise ApiError(400, "validation_error", f"Поле «{label}» слишком длинное")
        return attr, value.replace("\r\n", "\n").strip() if api_name == "name" else value
    if kind == "short":
        if value in (None, ""):
            return attr, None
        if not isinstance(value, str):
            raise ApiError(400, "validation_error", f"Поле «{label}» должно быть строкой")
        return attr, value.strip()[:SHORT_MAX.get(api_name, 500)] or None
    if kind == "key":
        if value in (None, ""):
            return attr, None
        return attr, check_key(value, label)
    if kind == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ApiError(400, "validation_error", f"Поле «{label}» должно быть числом")
        return attr, float(value)
    if kind == "int":
        if value in (None, ""):
            return attr, None
        if not isinstance(value, int) or isinstance(value, bool) or abs(value) > 100000:
            raise ApiError(400, "validation_error", f"Поле «{label}» должно быть целым числом")
        return attr, value
    if kind == "progress":
        if value in (None, ""):
            return attr, 0
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ApiError(400, "validation_error", "Прогресс должен быть числом")
        return attr, max(0, min(100, int(round(value))))
    if kind == "date":
        parsed = parse_date(value, label)
        return attr, parsed
    if kind == "status":
        if value not in STATUS_LABELS:
            raise ApiError(400, "validation_error", "Неизвестный статус")
        return attr, value
    if kind == "end_mode":
        if value not in ("date", "duration"):
            raise ApiError(400, "validation_error", "Неизвестный режим завершения")
        return attr, value
    if kind == "duration_type":
        if value not in ("calendar", "working"):
            raise ApiError(400, "validation_error", "Неизвестный тип дней")
        return attr, value
    if kind == "dependency_type":
        if value not in ("FS", "SS", "FF", "SF"):
            raise ApiError(400, "validation_error", "Неизвестный тип связи")
        return attr, value
    if kind == "color":
        if value in (None, ""):
            return attr, None
        if not is_color(value):
            raise ApiError(400, "validation_error", "Неверный цвет")
        return attr, value
    if kind == "line_style":
        if value not in ("solid", "dashed", "dotted"):
            raise ApiError(400, "validation_error", "Неизвестный стиль линии")
        return attr, value
    if kind == "url":
        return attr, safe_url(value)
    raise AssertionError(kind)


def item_to_api(item):
    data = {
        "key": item.key,
        "type": item.type,
        "name": item.name or "",
        "parentKey": item.parent_key,
        "position": item.position,
        "color": item.color,
        "assigneeKey": item.assignee_key,
        "commentCount": item.comment_count or 0,
        "updatedAt": item.updated_at.replace(microsecond=0).isoformat() + "Z" if item.updated_at else None,
        "updatedById": item.updated_by_id,
        "updatedRev": item.updated_rev,
    }
    if item.extra:
        # Служебные поля переноса из 1.0 (например, группа строк 1.0): копия и версии плана их сохраняют
        data["extra"] = item.extra
    if item.type == "task":
        data.update({
            "status": item.status or "new",
            "start": item.start_date.isoformat() if item.start_date else None,
            "end": item.end_date.isoformat() if item.end_date else None,
            "endMode": item.end_mode or "date",
            "duration": item.duration,
            "durationType": item.duration_type or "calendar",
            "progress": item.progress or 0,
            "offset": item.offset_days or 0,
            "completedDate": item.completed_date.isoformat() if item.completed_date else None,
            "dependencyKey": item.dependency_key,
            "dependencyType": item.dependency_type or "FS",
            "dependencyLag": item.dependency_lag or 0,
            "category": item.category,
            "barText": item.bar_text,
            "jiraUrl": item.jira_url,
        })
    elif item.type == "milestone":
        data.update({
            "date": item.start_date.isoformat() if item.start_date else None,
            "comment": item.comment,
            "lineStyle": item.line_style or "dashed",
            "lineColor": item.line_color or item.color,
        })
    if item.deleted_at is not None:
        data["deleted"] = True
    return data


def note_to_api(note):
    data = {
        "key": note.key,
        "date": note.date.isoformat(),
        "text": note.text or "",
        "rowKey": note.row_key,
        "color": note.color,
        "showDate": bool(note.show_date),
        "updatedRev": note.updated_rev,
    }
    if note.deleted_at is not None:
        data["deleted"] = True
    return data


def assignee_to_api(assignee):
    data = {
        "key": assignee.key,
        "name": assignee.name,
        "role": assignee.role or "",
        "color": assignee.color or "#2563eb",
        "userId": assignee.user_id,
        "position": assignee.position,
        "updatedRev": assignee.updated_rev,
    }
    if assignee.deleted_at is not None:
        data["deleted"] = True
    return data
