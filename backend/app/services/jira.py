"""Подключение к Jira и задачи, привязанные к строкам планов (ADR-0007).

Подключение хранится в app_settings['jira']; токен — зашифрованным. Данные задач кэшируются в jira_issues
на JIRA_CACHE_SECONDS: открытие плана читает кэш и спрашивает Jira только об устаревших задачах, кнопка
«Обновить из Jira» перечитывает все. Если Jira не отвечает, следующие минуту план открывается с данными
из кэша и пометкой, без ожидания Jira на каждом открытии (каждый процесс gunicorn считает это сам).
"""

import time
from datetime import timedelta

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.jira_client import (
    JiraClient, JiraError, check_base_url, decrypt_token, issue_key_from_url, normalize_issue, request_fields,
)
from app.models import AppSetting, JiraIssue, PlanItem, utcnow

SETTING_KEY = "jira"
DEFAULT_CONNECTION = {
    "baseUrl": "", "instanceType": "server_basic", "login": "", "tokenEncrypted": "", "acceptInvalidCerts": False,
    "startField": "", "endField": "duedate",
}
NOT_CONFIGURED = "Подключение к Jira не настроено — обратитесь к администратору (раздел «Интеграции»)"
MAX_PLAN_ISSUES = 1000
DOWN_SECONDS = 60          # после отказа Jira — минута без запросов к ней при открытии планов
REFRESH_PAUSE_SECONDS = 15  # «Обновить из Jira» сразу после отказа тоже не идёт к Jira
FETCH_BUDGET_SECONDS = 15   # дольше одно открытие плана Jira не ждёт: остальное — при следующем
CACHE_DAYS = 30             # задачи, которые никто не открывал месяц, удаляются из кэша
MISSING_CACHE_SECONDS = 60  # «нет в Jira или нет доступа» считается свежим минуту
_down_until = 0.0
_down_since = 0.0
EPIC_FIELD_SECONDS = 600    # поле «Epic Link» ищется в /field не чаще раза в 10 минут (на процесс)
_epic_fields = {}           # (адрес Jira, тип подключения) → (поле, до какого времени)


def get_connection():
    """Сохранённое подключение или None."""
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is None or not isinstance(row.value, dict) or not row.value.get("baseUrl"):
        return None
    return {**DEFAULT_CONNECTION, **{k: v for k, v in row.value.items() if k in DEFAULT_CONNECTION}}


def save_connection(data):
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is None:
        db.session.add(AppSetting(key=SETTING_KEY, value=data))
    else:
        row.value = data


def delete_connection():
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is not None:
        db.session.delete(row)
    clear_cache()
    _epic_fields.clear()


def reset_backoff():
    """Подключение сохранено заново — пробуем Jira сразу, не дожидаясь конца паузы после отказа; поле «Epic Link»
    ищется заново."""
    global _down_until, _down_since
    _down_until = _down_since = 0.0
    _epic_fields.clear()


def epic_link_field(client):
    """Поле «Epic Link» подключённой Jira — из кэша на EPIC_FIELD_SECONDS: список полей (/field) у Jira большой и
    меняется редко, а «Задачи из Jira» ищут часто."""
    key = (client.base_url, client.instance_type)
    cached = _epic_fields.get(key)
    if cached is not None and cached[1] > time.monotonic():
        return cached[0]
    value = client.epic_link_field()
    _epic_fields[key] = (value, time.monotonic() + EPIC_FIELD_SECONDS)
    return value


def clear_cache():
    """Сменилась Jira или поля дат — данные кэша относятся к прежнему подключению."""
    db.session.query(JiraIssue).delete()


def field_map(conn):
    return {"startField": conn.get("startField") or "", "endField": conn.get("endField") or "duedate"}


def admin_json(conn):
    """Подключение для формы администратора — без токена."""
    if not conn:
        return None
    return {"baseUrl": conn["baseUrl"], "instanceType": conn["instanceType"], "login": conn["login"] or "",
            "acceptInvalidCerts": bool(conn["acceptInvalidCerts"]), "hasToken": bool(conn["tokenEncrypted"]),
            **field_map(conn)}


def public_json(conn):
    """Что видят все сотрудники: есть ли подключение и адрес Jira (для ссылок по ключу задачи)."""
    if not conn:
        return {"connected": False, "baseUrl": None, "hasStartField": False}
    return {"connected": True, "baseUrl": conn["baseUrl"], "hasStartField": bool(conn.get("startField"))}


def make_client(override=None, detailed=False):
    """Клиент по сохранённому подключению; override — данные формы администратора (проверка до сохранения).
    Без нового токена в форме используется сохранённый — только для того же адреса Jira."""
    saved = get_connection()
    if override is None:
        if not saved:
            raise JiraError(NOT_CONFIGURED)
        conf = saved
        token = decrypt_token(current_app.secret_key, saved["tokenEncrypted"]) if saved["tokenEncrypted"] else ""
    else:
        conf = override
        token = override.get("token") or ""
        if not token:
            same_jira = saved and saved["tokenEncrypted"] and \
                check_base_url(override.get("baseUrl")) == saved["baseUrl"]
            if not same_jira:
                raise JiraError("Укажите пароль или токен")
            token = decrypt_token(current_app.secret_key, saved["tokenEncrypted"])
    if not token:
        raise JiraError("Укажите пароль или токен")
    if conf["instanceType"] != "server" and not conf.get("login"):
        raise JiraError("Укажите email или логин")
    return JiraClient(conf["baseUrl"], conf["instanceType"], conf.get("login") or "", token,
                      accept_invalid_certs=bool(conf.get("acceptInvalidCerts")), detailed=detailed)


def _store(rows):
    """Запись кэша одним запросом; одну задачу могут обновлять несколько процессов сразу — побеждает последний."""
    if not rows:
        return
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(JiraIssue).values(rows)
    db.session.execute(stmt.on_conflict_do_update(index_elements=[JiraIssue.key], set_={
        "found": stmt.excluded.found, "data": stmt.excluded.data, "fetched_at": stmt.excluded.fetched_at}))


def plan_issue_keys(plan_id, base_url):
    """{ключ задачи Jira: [ключи строк]} по ссылкам строк плана на подключённую Jira."""
    out = {}
    rows = db.session.execute(select(PlanItem.key, PlanItem.jira_url).where(
        PlanItem.plan_id == plan_id, PlanItem.deleted_at.is_(None), PlanItem.jira_url.is_not(None))).all()
    for item_key, url in rows:
        issue_key = issue_key_from_url(url, base_url)
        if issue_key:
            out.setdefault(issue_key, []).append(item_key)
    return out


def fetch_issues(keys, refresh=False, detailed=False):
    """{ключ: данные или None (нет в Jira или нет прав)} для ключей; error — текст, если Jira не ответила
    (тогда возвращаются данные кэша). Свежие записи кэша Jira не перечитывает, если не refresh."""
    global _down_until, _down_since
    conn = get_connection()
    if not conn:
        raise JiraError(NOT_CONFIGURED)
    keys = sorted(set(keys))[:MAX_PLAN_ISSUES]
    cached = {row.key: row for row in db.session.execute(select(JiraIssue).where(JiraIssue.key.in_(keys))).scalars()}
    now = utcnow()
    fresh_after = now - timedelta(seconds=current_app.config.get("JIRA_CACHE_SECONDS", 300))
    # «Нет в Jira» перепроверяется через минуту: задачу могли только что создать или выдать на неё права
    missing_fresh_after = max(fresh_after, now - timedelta(seconds=MISSING_CACHE_SECONDS))

    def is_stale(key):
        row = cached.get(key)
        return row is None or row.fetched_at < (fresh_after if row.found else missing_fresh_after)

    stale = keys if refresh else [k for k in keys if is_stale(k)]
    error, fetched_at = None, None
    now_mono = time.monotonic()
    available = now_mono >= _down_until or (refresh and now_mono - _down_since >= REFRESH_PAUSE_SECONDS)
    if stale and available:
        try:
            client = make_client(detailed=detailed)
            fmap = field_map(conn)
            found, checked = client.issues_by_keys(stale, request_fields(fmap),
                                                   deadline=now_mono + FETCH_BUDGET_SECONDS)
            now = utcnow()
            _store([{"key": key, "found": key in found, "fetched_at": now,
                     "data": normalize_issue(key, found[key], conn["baseUrl"], fmap) if key in found else None}
                    for key in stale if key in checked])
            db.session.query(JiraIssue).filter(JiraIssue.fetched_at < now - timedelta(days=CACHE_DAYS)).delete()
            db.session.commit()
            _down_until = 0.0
            if len(checked) < len(stale):
                error = "Jira отвечает медленно — часть задач обновится при следующем открытии плана"
            cached.update({row.key: row for row in db.session.execute(
                select(JiraIssue).where(JiraIssue.key.in_(stale)).execution_options(populate_existing=True)
            ).scalars()})
        except JiraError as exc:
            db.session.rollback()
            # Отказ (сеть, 5xx, неверный пароль или токен, нет прав): минута без запросов к Jira — иначе каждое
            # открытие плана ждёт её, а неверный пароль быстро приводит учётную запись к CAPTCHA
            if exc.status not in (400, 404):
                _down_until = time.monotonic() + DOWN_SECONDS
                _down_since = time.monotonic()
            error = str(exc)
    elif stale:
        error = "Jira недавно не ответила — показаны сохранённые данные"
    issues = {}
    for key in keys:
        row = cached.get(key)
        if row is not None:
            issues[key] = row.data if row.found else None
            fetched_at = row.fetched_at if fetched_at is None or row.fetched_at < fetched_at else fetched_at
    return issues, error, fetched_at
