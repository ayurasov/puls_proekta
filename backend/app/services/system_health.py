"""Состояние системы (ADR-0014): всё, от чего зависит работа «Пульса», — одной страницей.

Каждый компонент — карточка с состоянием ok (в порядке), warn (требует внимания), error (неполадка) или off (не
настроено). Состояние считается при каждом запросе страницы по живой базе и окружению; истории нет.

Что запоминается между запросами (app_settings):

* health_state — итог последних обращений к Jira, «По Делу» и каталогу (LDAP): клиенты сообщают о каждом запросе
  через note(); удачные запросы записываются не чаще раза в минуту, сбой — сразу;
* server_errors — последние 50 необработанных ошибок сервера (500): время, метод, адрес, тип ошибки;
* backup_status — итог последней резервной копии: его записывает deploy/backup.sh после каждой копии.

В запросе события копятся в flask.g и записываются после ответа (teardown_request) отдельной транзакцией: так
сбой Jira, после которого запрос откатывает свою транзакцию, всё равно попадает в состояние.
"""

import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

from flask import current_app, g, has_request_context, request
from sqlalchemy import func, select, text

from app.extensions import db
from app.models import AppSetting, CalendarDay, IntegrationKey, Plan, PlanItem, Project, User

log = logging.getLogger(__name__)

STARTED_AT = datetime.now(timezone.utc).replace(tzinfo=None)
STATE_KEY, ERRORS_KEY, BACKUP_KEY = "health_state", "server_errors", "backup_status"
OK_WRITE_INTERVAL = timedelta(minutes=1)
ERRORS_KEEP = 50
ERRORS_WINDOW = timedelta(hours=24)
# Порог «много ошибок» — за последний час
ERRORS_BURST = 20
DISK_MIN_FREE = 1024 ** 3
SLOW_DB_MS = 500
ORDER = {"ok": 0, "off": 0, "warn": 1, "error": 2}
COMPONENTS = ("app", "backups", "errors", "jira", "podelu", "ldap", "keys", "calendar", "users", "data")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    return value.replace(microsecond=0).isoformat() + "Z" if value else None


def _parse(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def ago(value, now=None):
    """«5 мин назад» — по-русски, как в burndown."""
    if value is None:
        return "никогда"
    seconds = ((now or utcnow()) - value).total_seconds()
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    if seconds < 48 * 3600:
        return f"{int(seconds // 3600)} ч назад"
    return f"{int(seconds // 86400)} дн. назад"


def fmt_bytes(size):
    value = float(size or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return (f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}").replace(".", ",")
        value /= 1024
    return ""


def _setting(key, default):
    row = db.session.get(AppSetting, key)
    value = row.value if row is not None else None
    return value if isinstance(value, type(default)) else default


def _save(key, value):
    row = db.session.get(AppSetting, key)
    if row is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


# ---------------------------------------------------------------- учёт событий

def note(component, ok, message=""):
    """Итог обращения к внешней системе (jira, podelu, podelu_links, ldap). Вне запроса не записывается."""
    if not has_request_context():
        return
    g.setdefault("health_events", []).append((component, bool(ok), (message or "")[:300], utcnow()))


def reporter(component):
    return lambda ok, message: note(component, ok, message)


def note_server_error(exc):
    """Необработанная ошибка сервера (ответ 500)."""
    if not has_request_context():
        return
    g.setdefault("health_errors", []).append({
        "at": _iso(utcnow()), "method": request.method, "path": request.path[:200],
        "error": f"{type(exc).__name__}: {str(exc)[:200]}".rstrip(": "),
    })


def flush(_exc=None):
    """Записать события запроса отдельной транзакцией (teardown_request; в «Проверить подключения» — сразу)."""
    if not has_request_context():
        return
    events, errors = g.pop("health_events", None), g.pop("health_errors", None)
    if not events and not errors:
        return
    try:
        db.session.rollback()
        if events:
            _record(events)
        if errors:
            _save(ERRORS_KEY, (errors + _setting(ERRORS_KEY, []))[:ERRORS_KEEP])
        db.session.commit()
    except Exception:  # noqa: BLE001 — состояние системы не должно ломать ответ
        db.session.rollback()
        log.warning("Не удалось записать состояние системы", exc_info=True)


def _record(events):
    state = dict(_setting(STATE_KEY, {}))
    changed = False
    for component, ok, message, at in events:
        current = dict(state.get(component) or {})
        if ok:
            last_ok = _parse(current.get("lastOkAt"))
            if current.get("last") == "ok" and last_ok and at - last_ok < OK_WRITE_INTERVAL:
                continue
            current.update(last="ok", lastOkAt=_iso(at))
        else:
            current.update(last="error", lastErrorAt=_iso(at), lastError=message)
        state[component] = current
        changed = True
    if changed:
        _save(STATE_KEY, state)


def install(app):
    app.teardown_request(flush)


# ---------------------------------------------------------------- компоненты

def _item(key, title, status, summary, details=(), link=None, capability=None):
    return {"key": key, "title": title, "status": status, "summary": summary,
            "details": [d for d in details if d], "link": link, "capability": capability}


def _alembic_head():
    cached = getattr(_alembic_head, "value", None)
    if cached is None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = os.path.dirname(current_app.root_path)
        config = Config(os.path.join(root, "alembic.ini"))
        config.set_main_option("script_location", os.path.join(root, "alembic"))
        cached = ScriptDirectory.from_config(config).get_current_head() or ""
        _alembic_head.value = cached
    return cached


def _app_item(now):
    version = current_app.config.get("APP_VERSION", "")
    details, status = [], "ok"
    engine = db.engine
    started = time.perf_counter()
    db.session.execute(text("SELECT 1"))
    latency = (time.perf_counter() - started) * 1000
    if engine.dialect.name == "postgresql":
        server = db.session.execute(text("SHOW server_version")).scalar() or ""
        size = db.session.execute(text("SELECT pg_database_size(current_database())")).scalar()
        details.append(f"База данных: PostgreSQL {server.split(' ')[0]}, размер {fmt_bytes(size)}")
    else:
        path = engine.url.database or ""
        details.append(f"База данных: {engine.dialect.name}" + (f", файл {os.path.basename(path)}" if path else ""))
        if path and os.path.exists(path):
            details.append(f"Размер базы: {fmt_bytes(os.path.getsize(path))}")
            try:
                usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)))
                details.append(f"Свободно на диске: {fmt_bytes(usage.free)} из {fmt_bytes(usage.total)}")
                if usage.free < DISK_MIN_FREE or usage.free < usage.total * 0.05:
                    status = "warn"
                    details.append("Места на диске мало: освободите его, иначе база перестанет сохранять изменения")
            except OSError as exc:
                status = "warn"
                details.append(f"Не удалось проверить диск: {exc}")
    details.append(f"Ответ базы: {latency:.0f} мс".replace(".", ","))
    if latency > SLOW_DB_MS:
        status = "warn"
        details.append("База отвечает медленно — проверьте нагрузку на сервер базы данных")
    try:
        revision = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 — база создана без миграций (тесты, разработка)
        db.session.rollback()
        revision = None
    head = _alembic_head()
    summary = f"Версия {version}; служба запущена {ago(STARTED_AT, now)}"
    if revision and head and revision != head:
        status = "error"
        summary = f"Схема базы не обновлена: применена {revision}, нужна {head} — выполните обновление"
    elif revision:
        details.append(f"Схема базы: {revision}")
    return _item("app", "Приложение", status, summary, details)


def _backups_item(now):
    """Резервные копии (ADR-0015): работает ли служба копий, по расписанию ли последняя копия, удалась ли она."""
    from app.services import backups

    info = _setting(BACKUP_KEY, {})
    link, capability = "/admin/backups", "system.health"
    title = "Резервные копии"
    if not info:
        return _item("backups", title, "off", "Резервное копирование не настроено", [
            "Копии делает контейнер backup (docker compose up -d backup): после его запуска здесь появятся "
            "расписание, время и размер копий",
        ], link, capability)
    last_ok = _parse(info.get("lastOkAt"))
    service = info.get("service") if isinstance(info.get("service"), dict) else None
    seen = _parse(info.get("seenAt"))
    details = []
    if service:
        details.append(f"Расписание: {backups.schedule_text(service)}"
                       + ("" if service.get("source") == "settings" else " (из настроек сервера)"))
    if info.get("count") is not None:
        details.append(f"Копий хранится: {info['count']}, всего {fmt_bytes(info.get('totalBytes'))}")
    if service and seen and now - seen > backups.SERVICE_STALE:
        return _item("backups", title, "error", f"Служба резервного копирования не отвечает: последний раз "
                     f"{ago(seen, now)} — проверьте контейнер backup", details, link, capability)
    if info.get("ok") is False:
        details.insert(0, f"Последняя удачная копия: {ago(last_ok, now)}")
        failed = _parse(info.get("at"))
        return _item("backups", title, "error",
                     f"Последняя копия не удалась ({ago(failed, now)}): {info.get('error') or 'ошибка pg_dump'}",
                     details, link, capability)
    if service and not service.get("enabled"):
        return _item("backups", title, "warn", "Копии по расписанию выключены"
                     + (f"; последняя — {ago(last_ok, now)}" if last_ok else ""), details, link, capability)
    if last_ok is None:
        return _item("backups", title, "warn", "Удачных копий ещё не было", details, link, capability)
    # Сколько можно ждать: промежуток между днями расписания (у ежедневного — сутки) и запас
    gap = backups.max_gap_days(service.get("days")) if service else 1
    period = timedelta(days=gap or 1)
    age = now - last_ok
    size = f", {fmt_bytes(info.get('size'))}" if info.get("size") else ""
    if age > period + backups.GRACE_ERROR:
        return _item("backups", title, "error",
                     f"Копий нет дольше, чем по расписанию: последняя {ago(last_ok, now)}", details, link, capability)
    if age > period + backups.GRACE_WARN:
        return _item("backups", title, "warn",
                     f"Копия по расписанию запаздывает: последняя {ago(last_ok, now)}", details, link, capability)
    upcoming = backups.next_run(service, now, info.get("lastDailyDate")) if service else None
    if upcoming:
        local = upcoming + backups.offset(service.get("utcOffset"))
        details.insert(0, f"Следующая копия: {local.strftime('%d.%m в %H:%M')}")
    return _item("backups", title, "ok", f"Последняя копия {ago(last_ok, now)}{size}", details, link, capability)


def _errors_item(now):
    recent = [e for e in _setting(ERRORS_KEY, []) if isinstance(e, dict)
              and (_parse(e.get("at")) or datetime.min) >= now - ERRORS_WINDOW]
    if not recent:
        return _item("errors", "Ошибки сервера", "ok", "За сутки ошибок нет")
    hour = sum(1 for e in recent if _parse(e.get("at")) >= now - timedelta(hours=1))
    details = [f"{ago(_parse(e.get('at')), now)} · {e.get('method')} {e.get('path')} · {e.get('error')}"
               for e in recent[:5]]
    if hour >= ERRORS_BURST:
        return _item("errors", "Ошибки сервера", "error", f"Много ошибок: {hour} за последний час", details)
    return _item("errors", "Ошибки сервера", "warn", f"Ошибок за сутки: {len(recent)}", details)


def _calls(state, now, never="обращений ещё не было"):
    """(status, summary, details) по итогу последних обращений."""
    last_ok, last_error = _parse(state.get("lastOkAt")), _parse(state.get("lastErrorAt"))
    if state.get("last") == "error":
        return "error", f"Последнее обращение {ago(last_error, now)} — ошибка: {state.get('lastError') or 'нет ответа'}", \
            [f"Последнее успешное обращение: {ago(last_ok, now)}"]
    if last_ok:
        return "ok", f"Последнее успешное обращение {ago(last_ok, now)}", []
    return "ok", f"Подключена, {never}", []


def _host(url):
    from urllib.parse import urlsplit

    return urlsplit(url or "").netloc or url or ""


def _jira_item(state, now):
    from app.services import jira as jira_service

    conn = jira_service.get_connection()
    link = "/admin/integrations/jira"
    if not conn:
        return _item("jira", "Jira", "off", "Не подключена", link=link, capability="modules.manage")
    status, summary, details = _calls(state.get("jira") or {}, now)
    kind = {"cloud": "Jira Cloud", "server": "Server/DC · PAT", "server_basic": "Server/DC · логин и пароль"}
    details.insert(0, f"{_host(conn.get('baseUrl'))} · {kind.get(conn.get('instanceType'), '')}".strip(" ·"))
    return _item("jira", "Jira", status, summary, details, link, "modules.manage")


def _podelu_item(state, now):
    from app.services import podelu as podelu_service
    from app.services import podelu_links as links

    link = "/admin/integrations/podelu"
    structure = podelu_service.get_connection()
    settings = links.get_settings()
    has_links = bool(settings.get("baseUrl") and settings.get("keyEncrypted"))
    if not structure and not has_links:
        return _item("podelu", "«По Делу»", "off", "Не подключена", link=link, capability="modules.manage")
    parts = []
    if has_links:
        parts.append(("Ссылки из задач", _calls(state.get("podelu_links") or {}, now)))
    if structure:
        parts.append(("Структура и сотрудники", _calls(state.get("podelu") or {}, now)))
    worst = max(parts, key=lambda p: ORDER[p[1][0]])
    status = worst[1][0]
    summary = worst[1][1] if status != "ok" or len(parts) == 1 else parts[0][1][1]
    details = [_host(settings.get("baseUrl") or (structure or {}).get("baseUrl"))]
    for label, (part_status, part_summary, extra) in parts:
        details.append(f"{label}: {part_summary[0].lower()}{part_summary[1:]}")
        details.extend(extra if part_status != "ok" else [])
    if has_links and settings.get("checkedAt"):
        details.append(f"Связь проверена: {ago(_parse(settings['checkedAt']), now)}")
    return _item("podelu", "«По Делу»", status, summary, details, link, "modules.manage")


def _ldap_item(state, now):
    from app.ldap_auth import get_ldap_config

    config = get_ldap_config()
    link = "/admin/integrations/ldap"
    if not config.get("enabled"):
        return _item("ldap", "Вход через домен", "off", "Выключен", link=link, capability="admin")
    status, summary, details = _calls(state.get("ldap") or {}, now, never="входов через домен ещё не было")
    if status == "ok" and _parse((state.get("ldap") or {}).get("lastOkAt")):
        summary = f"Каталог отвечает: последнее обращение {ago(_parse(state['ldap']['lastOkAt']), now)}"
    elif status == "ok":
        summary = "Включён, входов через домен ещё не было"
    mode = {"local": "только пароли системы", "ldap": "только домен", "hybrid": "сначала домен, затем пароль"}
    details.insert(0, f"{_host((config.get('serverUrl') or '').replace('ldaps:', 'https:').replace('ldap:', 'http:'))}"
                      f" · {mode.get(config.get('authMode'), '')}".strip(" ·"))
    return _item("ldap", "Вход через домен", status, summary, details, link, "admin")


def _keys_item(now):
    keys = db.session.execute(select(IntegrationKey).where(IntegrationKey.revoked_at.is_(None))
                              .order_by(IntegrationKey.id)).scalars().all()
    link = "/admin/integrations/keys"
    if not keys:
        return _item("keys", "Ключи доступа", "off", "Действующих ключей нет", link=link, capability="admin")
    used = [k.last_used_at for k in keys if k.last_used_at]
    summary = f"Действующих ключей: {len(keys)}" + (f"; последнее обращение {ago(max(used), now)}" if used else "")
    details = [f"{k.name}: " + (f"обращение {ago(k.last_used_at, now)}" if k.last_used_at else "обращений не было")
               for k in keys[:6]]
    return _item("keys", "Ключи доступа", "ok", summary, details, link, "admin")


def _calendar_item(now):
    year = now.year
    counts = dict(db.session.execute(
        select(func.extract("year", CalendarDay.day), func.count()).group_by(func.extract("year", CalendarDay.day))
    ).all())
    counts = {int(y): n for y, n in counts.items()}
    link = "/admin/settings"
    details = [f"{y} год: {counts[y]} праздников и переносов" for y in sorted(counts) if y >= year - 1][:3]
    if not counts.get(year):
        return _item("calendar", "Производственный календарь", "warn",
                     f"Нет календаря на {year} год: праздники не учитываются в сроках", details, link,
                     "calendar.manage")
    if now.month >= 10 and not counts.get(year + 1):
        return _item("calendar", "Производственный календарь", "warn",
                     f"Нет календаря на {year + 1} год — внесите праздники до января", details, link,
                     "calendar.manage")
    years = f"{year} и {year + 1} годы" if counts.get(year + 1) else f"{year} год"
    return _item("calendar", "Производственный календарь", "ok", f"Календарь на {years} внесён", details, link,
                 "calendar.manage")


def _users_item(now):
    users = db.session.execute(select(User).where(User.is_active.is_(True))).scalars().all()
    admins = sum(1 for u in users if u.role == "admin")
    day = sum(1 for u in users if u.last_login_at and u.last_login_at >= now - timedelta(hours=24))
    delegated = sum(1 for u in users if u.role != "admin" and u.capabilities)
    domain = sum(1 for u in users if u.auth_source == "ldap")
    details = [f"Администраторов: {admins}" + (" — назначьте второго, чтобы не потерять доступ" if admins == 1 else ""),
               f"С полномочиями без роли администратора: {delegated}" if delegated else "",
               f"Входят через домен: {domain}" if domain else ""]
    return _item("users", "Сотрудники и вход", "ok", f"Активных: {len(users)}; входили за сутки: {day}", details,
                 "/admin/users", "users.manage")


def _data_item():
    projects = db.session.execute(select(func.count()).select_from(Project).where(Project.deleted_at.is_(None))).scalar()
    plans = db.session.execute(select(func.count()).select_from(Plan).join(Project, Project.id == Plan.project_id)
                               .where(Plan.deleted_at.is_(None), Project.deleted_at.is_(None))).scalar()
    rows = db.session.execute(select(func.count()).select_from(PlanItem).where(PlanItem.deleted_at.is_(None))).scalar()
    trash = db.session.execute(select(func.count()).select_from(Project).where(Project.deleted_at.is_not(None))).scalar() \
        + db.session.execute(select(func.count()).select_from(Plan).where(Plan.deleted_at.is_not(None))).scalar()
    return _item("data", "Данные", "ok", f"Проектов: {projects}; планов: {plans}; строк: {rows}",
                 [f"В корзине: {trash}" if trash else "Корзина пуста"], "/admin/trash", "trash.manage")


def overview(now=None):
    now = now or utcnow()
    state = _setting(STATE_KEY, {})
    builders = (
        lambda: _app_item(now), lambda: _backups_item(now), lambda: _errors_item(now),
        lambda: _jira_item(state, now), lambda: _podelu_item(state, now), lambda: _ldap_item(state, now),
        lambda: _keys_item(now), lambda: _calendar_item(now), lambda: _users_item(now), _data_item,
    )
    items = []
    for key, build in zip(COMPONENTS, builders, strict=True):
        try:
            items.append(build())
        except Exception as exc:  # noqa: BLE001 — одна карточка не должна ронять страницу
            db.session.rollback()
            log.warning("Состояние системы: компонент %s", key, exc_info=True)
            items.append(_item(key, key, "error", f"Не удалось проверить: {type(exc).__name__}"))
    status = max((i["status"] for i in items), key=lambda s: ORDER[s], default="ok")
    return {"status": "ok" if status == "off" else status, "checkedAt": _iso(now), "startedAt": _iso(STARTED_AT),
            "version": current_app.config.get("APP_VERSION", ""), "items": items}
