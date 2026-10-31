"""Подключение к Jira и задачи, привязанные к строкам планов (ADR-0007).

Подключение хранится в app_settings['jira']; токен — зашифрованным. Данные задач кэшируются в jira_issues
на JIRA_CACHE_SECONDS: открытие плана читает кэш и спрашивает Jira только об устаревших задачах, кнопка
«Обновить из Jira» перечитывает все. Если Jira не отвечает, следующие минуту план открывается с данными
из кэша и пометкой, без ожидания Jira на каждом открытии (каждый процесс gunicorn считает это сам).
"""

import re
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy import select

from app.extensions import db
from app.jira_client import (
    JiraClient, JiraError, check_base_url, decrypt_token, issue_key_from_url, normalize_issue, request_fields,
)
from app.models import AppSetting, JiraIssue, Plan, PlanItem, Project, utcnow

SETTING_KEY = "jira"
DEFAULT_CONNECTION = {
    "baseUrl": "", "instanceType": "server_basic", "login": "", "tokenEncrypted": "", "acceptInvalidCerts": False,
    "startField": "", "endField": "duedate",
    # Другие адреса этой же Jira (старый домен, адрес по IP, адрес из serverInfo): ссылки строк на них тоже
    # распознаются как задачи подключённой Jira. Запросы к Jira идут только на baseUrl.
    "linkUrls": [],
}
MAX_LINK_URLS = 10
NOT_CONFIGURED = "Подключение к Jira не настроено — обратитесь к администратору (раздел «Интеграции»)"
MAX_PLAN_ISSUES = 1000
DOWN_SECONDS = 60          # после отказа Jira — минута без запросов к ней при открытии планов
REFRESH_PAUSE_SECONDS = 15  # «Обновить из Jira» сразу после отказа тоже не идёт к Jira
FETCH_BUDGET_SECONDS = 15   # дольше одно открытие плана Jira не ждёт: остальное — при следующем
CACHE_DAYS = 30             # задачи, которые никто не открывал месяц, удаляются из кэша
MISSING_CACHE_SECONDS = 60  # «нет в Jira или нет доступа» считается свежим минуту
_down_until = 0.0
_down_since = 0.0
EPIC_FIELD_SECONDS = 600    # поля «Epic Link» и «Sprint» ищутся в /field не чаще раза в 10 минут (на процесс)
_epic_fields = {}           # (адрес Jira, тип подключения) → ({epicField, sprintField}, до какого времени)
PROJECTS_SECONDS = 600      # список проектов Jira — не чаще раза в 10 минут (на процесс)
_projects = {}              # (адрес Jira, тип подключения) → (проекты, до какого времени)


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
    _projects.clear()


def reset_backoff():
    """Подключение сохранено заново — пробуем Jira сразу, не дожидаясь конца паузы после отказа; поле «Epic Link»
    ищется заново."""
    global _down_until, _down_since
    _down_until = _down_since = 0.0
    _epic_fields.clear()
    _projects.clear()


def special_fields(client):
    """Поля «Epic Link» и «Sprint» подключённой Jira — из кэша на EPIC_FIELD_SECONDS: список полей (/field) у Jira
    большой и меняется редко, а «Задачи из Jira» ищут часто."""
    key = (client.base_url, client.instance_type)
    cached = _epic_fields.get(key)
    if cached is not None and cached[1] > time.monotonic():
        return cached[0]
    value = client.special_fields()
    _epic_fields[key] = (value, time.monotonic() + EPIC_FIELD_SECONDS)
    return value


def epic_link_field(client):
    return special_fields(client)["epicField"]


def projects(client, refresh=False):
    """Проекты Jira, которые видит учётная запись подключения, — из кэша на PROJECTS_SECONDS."""
    key = (client.base_url, client.instance_type)
    cached = _projects.get(key)
    if not refresh and cached is not None and cached[1] > time.monotonic():
        return cached[0]
    value = client.projects()
    _projects[key] = (value, time.monotonic() + PROJECTS_SECONDS)
    return value


def clear_cache():
    """Сменилась Jira или поля дат — данные кэша относятся к прежнему подключению."""
    db.session.query(JiraIssue).delete()


def field_map(conn):
    return {"startField": conn.get("startField") or "", "endField": conn.get("endField") or "duedate"}


def link_bases(conn):
    """Адреса, ссылки на которые считаются задачами подключённой Jira: основной и «другие адреса»."""
    bases = [conn["baseUrl"]]
    for url in conn.get("linkUrls") or []:
        if isinstance(url, str) and url and url not in bases:
            bases.append(url)
    return bases


def key_from_url(url, conn):
    """Ключ задачи по ссылке строки — на основной или один из других адресов этой Jira."""
    for base in link_bases(conn):
        key = issue_key_from_url(url, base)
        if key:
            return key
    return None


_BROWSE_RE = re.compile(r"/(?:browse|issues)/[A-Za-z][A-Za-z0-9_]*-\d+/?$|[?&]selectedIssue=", re.IGNORECASE)


def foreign_host(url):
    """Хост ссылки, похожей на задачу Jira (…/browse/ABC-1), — для подсказки «ссылка на другую Jira»; иначе None."""
    if not url or not _BROWSE_RE.search(url):
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        return f"{host}:{parts.port}" if parts.port else host or None
    except ValueError:
        return None


def admin_json(conn):
    """Подключение для формы администратора — без токена."""
    if not conn:
        return None
    return {"baseUrl": conn["baseUrl"], "instanceType": conn["instanceType"], "login": conn["login"] or "",
            "acceptInvalidCerts": bool(conn["acceptInvalidCerts"]), "hasToken": bool(conn["tokenEncrypted"]),
            "linkUrls": list(conn.get("linkUrls") or []), **field_map(conn)}


def public_json(conn):
    """Что видят все сотрудники: есть ли подключение и адрес Jira (для ссылок по ключу задачи)."""
    if not conn:
        return {"connected": False, "baseUrl": None, "linkUrls": [], "hasStartField": False}
    return {"connected": True, "baseUrl": conn["baseUrl"], "linkUrls": list(conn.get("linkUrls") or []),
            "hasStartField": bool(conn.get("startField"))}


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


def plan_issue_keys(plan_id, conn, foreign=None):
    """{ключ задачи Jira: [ключи строк]} по ссылкам строк плана на подключённую Jira (основной и другие адреса).
    foreign — словарь, в который считаются строки со ссылками на задачи другой Jira: {хост: число строк}."""
    out = {}
    rows = db.session.execute(select(PlanItem.key, PlanItem.jira_url).where(
        PlanItem.plan_id == plan_id, PlanItem.deleted_at.is_(None), PlanItem.jira_url.is_not(None))).all()
    for item_key, url in rows:
        issue_key = key_from_url(url, conn)
        if issue_key:
            out.setdefault(issue_key, []).append(item_key)
        elif foreign is not None:
            host = foreign_host(url)
            if host:
                foreign[host] = foreign.get(host, 0) + 1
    return out


def link_census(conn, limit_hosts=5):
    """Ссылки строк всех планов на задачи Jira: сколько распознано и на какие другие адреса ведут остальные —
    для диагностики «соединено, но ничего не загружается»."""
    rows = db.session.execute(
        select(PlanItem.jira_url).join(Plan, Plan.id == PlanItem.plan_id).join(Project, Project.id == Plan.project_id)
        .where(PlanItem.deleted_at.is_(None), PlanItem.jira_url.is_not(None), Plan.deleted_at.is_(None),
               Project.deleted_at.is_(None))).scalars().all()
    recognized, foreign, keys = 0, {}, set()
    for url in rows:
        key = key_from_url(url, conn) if conn else None
        if key:
            recognized += 1
            keys.add(key)
            continue
        host = foreign_host(url)
        if host:
            foreign[host] = foreign.get(host, 0) + 1
    hosts = sorted(foreign.items(), key=lambda kv: -kv[1])[:limit_hosts]
    return {"recognized": recognized, "issues": len(keys), "sample": sorted(keys)[:5],
            "foreign": [{"host": h, "rows": n} for h, n in hosts], "foreignRows": sum(foreign.values())}


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
