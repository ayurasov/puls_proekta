"""Резервные копии (ADR-0015): расписание и хранение задаёт администратор в «Пульсе», копии делает контейнер backup.

Связь — через app_settings, без доступа приложения к файлам копий и к Docker:

* backup_settings — расписание и хранение ({enabled, time "ЧЧ:ММ", days [1..7], retentionDays, keepManualDays});
  служба читает их раз в минуту. Пока их нет, служба работает по BACKUP_HOUR и BACKUP_RETENTION_DAYS из .env;
* backup_request — «Сделать копию сейчас» ({requestedAt, by}); служба выполняет запрос в течение минуты и снимает его;
* backup_status — что сообщает служба: итог последней копии, список копий, действующее расписание (service) и
  отметка «служба работает» (seenAt, раз в 5 минут).
"""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import AppSetting

SETTINGS_KEY, REQUEST_KEY, STATUS_KEY = "backup_settings", "backup_request", "backup_status"
DEFAULTS = {"enabled": True, "time": "03:00", "days": [1, 2, 3, 4, 5, 6, 7], "retentionDays": 30, "keepManualDays": 90}
# Служба отмечается раз в 5 минут; дольше 15 — не работает
SERVICE_STALE = timedelta(minutes=15)
# Сколько ждать копию сверх промежутка между днями расписания: «требует внимания» и «неполадка»
GRACE_WARN, GRACE_ERROR = timedelta(hours=2), timedelta(hours=26)
LABELS = {"daily": "По расписанию", "manual": "Вручную", "pre-update": "Перед обновлением",
          "pre-restore": "Перед восстановлением"}
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.replace(microsecond=0).isoformat() + "Z" if value else None


def parse(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _get(key, default):
    row = db.session.get(AppSetting, key)
    value = row.value if row is not None else None
    return value if isinstance(value, type(default)) else default


def _put(key, value):
    row = db.session.get(AppSetting, key)
    if row is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def stored_settings():
    value = _get(SETTINGS_KEY, {})
    return {**DEFAULTS, **value} if value else None


def save_settings(values):
    _put(SETTINGS_KEY, values)


def status():
    return _get(STATUS_KEY, {})


def request():
    return _get(REQUEST_KEY, {}) or None


def put_request(by):
    _put(REQUEST_KEY, {"requestedAt": iso(utcnow()), "by": by})


def offset(value):
    """«+0300» → timedelta(hours=3); непонятное — UTC."""
    if not isinstance(value, str) or len(value) != 5 or value[0] not in "+-" or not value[1:].isdigit():
        return timedelta(0)
    sign = 1 if value[0] == "+" else -1
    return sign * timedelta(hours=int(value[1:3]), minutes=int(value[3:5]))


def schedule_text(schedule):
    if not schedule or not schedule.get("enabled"):
        return "по расписанию — выключены"
    days = sorted(set(schedule.get("days") or []))
    if days == list(range(1, 8)):
        when = "ежедневно"
    elif days == [1, 2, 3, 4, 5]:
        when = "по будням"
    else:
        when = ", ".join(WEEKDAYS[d - 1] for d in days if 1 <= d <= 7) or "ни в один день"
    return f"{when} в {schedule.get('time') or '03:00'}"


def max_gap_days(days):
    """Наибольший промежуток между соседними днями расписания (по кругу недели), в днях."""
    days = sorted({d for d in days or [] if 1 <= d <= 7})
    if not days:
        return None
    if len(days) == 1:
        return 7
    gaps = [b - a for a, b in zip(days, days[1:], strict=False)] + [days[0] + 7 - days[-1]]
    return max(gaps)


def next_run(schedule, now=None, done_today=None):
    """Время следующей плановой копии (UTC) по действующему расписанию и часовому поясу службы."""
    if not schedule or not schedule.get("enabled"):
        return None
    try:
        hour, minute = (int(p) for p in str(schedule.get("time") or "03:00").split(":"))
    except ValueError:
        return None
    shift = offset(schedule.get("utcOffset"))
    local_now = (now or utcnow()) + shift
    days = {d for d in schedule.get("days") or [] if 1 <= d <= 7}
    for ahead in range(0, 8):
        day = (local_now + timedelta(days=ahead)).date()
        if day.isoweekday() not in days:
            continue
        at = datetime(day.year, day.month, day.day, hour, minute)
        if ahead == 0:
            if done_today == day.isoformat():
                continue
            if at <= local_now:
                # Сегодняшняя ещё не сделана, а время прошло — служба догонит её в течение минуты
                return local_now - shift + timedelta(minutes=1)
        return at - shift
    return None


def overview(now=None):
    now = now or utcnow()
    info = status()
    stored = stored_settings()
    service = info.get("service") if isinstance(info.get("service"), dict) else None
    seen = parse(info.get("seenAt"))
    effective = service or ({**stored, "source": "settings"} if stored else None)
    files = [f for f in info.get("files") or [] if isinstance(f, dict)]
    upcoming = next_run(service, now, info.get("lastDailyDate")) if service else None
    running = info.get("running") if isinstance(info.get("running"), dict) else None
    return {
        "settings": stored or dict(DEFAULTS),
        "saved": stored is not None,
        "service": {
            "alive": bool(seen and now - seen <= SERVICE_STALE),
            "seenAt": iso(seen),
            "effective": effective,
            "scheduleText": schedule_text(effective),
        },
        "last": {
            "ok": info.get("ok"), "at": info.get("at"), "lastOkAt": info.get("lastOkAt"), "label": info.get("label"),
            "file": info.get("file"), "size": info.get("size"), "error": info.get("error"),
        } if info.get("at") or info.get("lastOkAt") else None,
        "running": running,
        "request": request(),
        "files": [{**f, "kind": LABELS.get(_label(f.get("name")), _label(f.get("name")))} for f in files],
        "count": info.get("count"),
        "totalBytes": info.get("totalBytes"),
        "nextAt": iso(upcoming),
        # По часовому поясу службы — как в расписании («03:00»), а не браузера
        "nextLocal": (upcoming + offset(service.get("utcOffset"))).strftime("%d.%m.%Y, %H:%M") if upcoming else None,
        "checkedAt": iso(now),
    }


def _label(name):
    # puls_20261105-030000_daily.dump → daily
    if not isinstance(name, str) or not name.endswith(".dump") or name.count("_") < 2:
        return ""
    return name[:-5].split("_", 2)[2]
