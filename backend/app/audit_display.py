"""Реестр изменений «для людей»: подписи разделов, объектов, полей и значений.

Записи хранятся как есть — в том числе перенесённые из 1.0.x («project #6», «user #8 (…)»,
поле «active» = «true», фото в base64). Историю не переписываем: всё переводится при показе
(страница «Реестр изменений» и выгрузка CSV), поэтому старые и новые записи читаются одинаково.
"""

import re
from datetime import date

from sqlalchemy import select

from app.extensions import db
from app.models import Direction, Project, User

SECTION_LABELS = {"Гант": "Планы", "Пользователи": "Сотрудники"}

FIELD_LABELS = {
    "title": "Название", "subtitle": "Описание", "name": "Название", "description": "Описание",
    "direction": "Направление", "color": "Цвет", "position": "Порядок", "level": "Уровень доступа",
    "kind": "Тип дня", "avatar": "Фото", "password": "Пароль", "role": "Роль", "is_active": "Активен",
    "active": "Активен", "login": "Логин", "username": "Логин", "email": "Email", "auth_source": "Способ входа",
    "data": "Данные плана",
}
USER_FIELD_LABELS = {"name": "ФИО", "position": "Должность"}

LDAP_FIELD_LABELS = {
    "enabled": "вход через домен", "serverUrl": "адрес сервера", "baseDN": "Base DN", "bindDN": "сервисная учётная запись",
    "bindPassword": "пароль сервисной учётной записи", "searchFilter": "фильтр поиска",
    "userAttributes": "атрибуты сотрудника", "authMode": "режим входа", "defaultRole": "роль новых сотрудников",
    "timeoutSeconds": "таймаут",
}

ROLE_LABELS = {"admin": "Администратор", "manager": "Менеджер", "editor": "Редактор", "viewer": "Наблюдатель"}
DAY_KINDS = {"holiday": "Нерабочий день", "workday": "Рабочий день"}
AUTH_SOURCES = {"local": "пароль системы", "ldap": "учётная запись домена"}
COLORS = {
    "blue": "Синий", "teal": "Бирюзовый", "green": "Зелёный", "orange": "Оранжевый", "red": "Красный",
    "purple": "Фиолетовый", "amber": "Янтарный", "pink": "Розовый", "slate": "Серый", "indigo": "Индиго",
    "cyan": "Голубой", "lime": "Лайм",
}

_PROJECT = re.compile(r"^project #(\d+)(?: \((.*)\))?$", re.S)
_PLAN = re.compile(r"^plan #(\d+)(?: \((.*)\))?$", re.S)
_USER = re.compile(r"^user #(\d+)(?: \((.*?)\))?(?: → (.*))?$", re.S)
_DIRECTION = re.compile(r"^direction #(\d+)$")
_CALENDAR = re.compile(r"^Календарь (\d{4}-\d{2}-\d{2})$")
_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _day(value):
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


class AuditLookups:
    """Имена сотрудников, проектов и направлений для пачки записей — одним запросом на справочник."""

    def __init__(self, rows):
        rows = list(rows)
        user_ids, project_ids, direction_ids, logins = set(), set(), set(), set()
        for r in rows:
            if r.user_id:
                user_ids.add(r.user_id)
            if r.user_name:
                logins.add(r.user_name)
            for pattern, bucket in ((_USER, user_ids), (_PROJECT, project_ids), (_DIRECTION, direction_ids)):
                match = pattern.match(r.entity or "")
                if match:
                    bucket.add(int(match.group(1)))
        users = db.session.execute(
            select(User.id, User.name, User.login).where(User.id.in_(user_ids) | User.login.in_(logins))
        ).all() if user_ids or logins else []
        self.user_names = {uid: name for uid, name, _login in users}
        self.login_names = {login: name for _uid, name, login in users}
        self.project_titles = dict(db.session.execute(
            select(Project.id, Project.title).where(Project.id.in_(project_ids))).all()) if project_ids else {}
        self.direction_names = dict(db.session.execute(
            select(Direction.id, Direction.name).where(Direction.id.in_(direction_ids))).all()) if direction_ids else {}

    def author(self, row):
        return self.user_names.get(row.user_id) or self.login_names.get(row.user_name) or row.user_name or "система"


def entity_label(row, lookups):
    entity = row.entity or ""
    match = _PROJECT.match(entity)
    if match:
        title = match.group(2) or row.project_title or lookups.project_titles.get(int(match.group(1)))
        return f"Проект «{title}»" if title else f"Проект №{match.group(1)}"
    match = _PLAN.match(entity)
    if match:
        return f"План «{match.group(2)}»" if match.group(2) else f"План №{match.group(1)}"
    match = _USER.match(entity)
    if match:
        name = lookups.user_names.get(int(match.group(1))) or match.group(2) or f"№{match.group(1)}"
        label = f"Сотрудник {name.strip()}"
        return f"{label} → {match.group(3)}" if match.group(3) else label
    match = _DIRECTION.match(entity)
    if match:
        name = lookups.direction_names.get(int(match.group(1)))
        if not name and row.field == "name":
            name = row.new_value or row.old_value
        return f"Направление «{name}»" if name else f"Направление №{match.group(1)}"
    match = _CALENDAR.match(entity)
    if match:
        return f"Производственный календарь, {_day(match.group(1))}"
    if entity == "LDAP":
        return "Вход через домен (LDAP)"
    return entity


def field_label(row):
    field = row.field or ""
    if row.entity == "LDAP" and field:
        return ", ".join(LDAP_FIELD_LABELS.get(part.strip(), part.strip()) for part in field.split(","))
    if (row.entity or "").startswith("user #") and field in USER_FIELD_LABELS:
        return USER_FIELD_LABELS[field]
    return FIELD_LABELS.get(field, field)


def value_label(row, value):
    if value is None or value == "":
        return ""
    field = row.field or ""
    if field == "avatar":
        if value.startswith("data:image"):
            return "фото обновлено"
        return value
    if field == "role":
        return ROLE_LABELS.get(value, value)
    if field in ("active", "is_active"):
        return {"true": "активен", "false": "отключён", "True": "активен", "False": "отключён"}.get(value, value)
    if field == "auth_source":
        return AUTH_SOURCES.get(value, value)
    if field == "color":
        return COLORS.get(value, value)
    if field == "kind":
        kind, sep, title = value.partition(": ")
        if kind in DAY_KINDS:
            return f"{DAY_KINDS[kind]}: {title}" if sep and title else DAY_KINDS[kind]
    match = _ISO_DAY.match(value)
    if match:
        return _day(value)
    return value


def display_row(row, lookups):
    return {
        "userName": lookups.author(row),
        "userLogin": row.user_name,
        "sectionLabel": SECTION_LABELS.get(row.section, row.section),
        "entityLabel": entity_label(row, lookups),
        "fieldLabel": field_label(row),
        "oldLabel": value_label(row, row.old_value),
        "newLabel": value_label(row, row.new_value),
    }
