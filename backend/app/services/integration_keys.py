"""Ключи интеграций для API `/api/integration/v1` (ADR-0011).

Каждая система получает свой ключ (`puls_…`) с правами (scopes); ключи выпускаются, перевыпускаются и отзываются
независимо. Хранится только SHA-256 ключа (уникальный индекс — ключ находится одним запросом, затем сверяется
`hmac.compare_digest`) и первые 12 символов для показа; сам ключ показывается один раз — при выпуске.

Права:

* `projects.read` — проекты, их планы и строки планов, направления (так работает «По Делу», ADR-0010);
* `projects.stats` — показатели проектов для сводных отчётов: задачи в работе и заблокированные, направление,
  время последнего изменения, окно плана («Дашборды компании»).

Каждое действие с ключом попадает в реестр изменений (раздел «Интеграции») — без самого ключа.
"""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import IntegrationKey, User, utcnow

KEY_PREFIX = "puls_"
KEY_RE = re.compile(r"^puls_[A-Za-z0-9_\-]{20,200}$")
SCOPES = {
    "projects.read": "Проекты, планы и строки планов, направления",
    "projects.stats": "Показатели проектов: в работе, заблокировано, направление, изменения, окно плана",
}
APPS = {
    "podelu": "«По Делу»",
    "dashboards": "«Дашборды компании»",
    "other": "Другая система",
}
DEFAULT_SCOPES = {"podelu": ["projects.read"], "dashboards": ["projects.read", "projects.stats"], "other": ["projects.read"]}
LAST_USED_EVERY = timedelta(minutes=1)
SECTION = "Интеграции"
MAX_ACTIVE = 50


class IntegrationKeyError(ValueError):
    """Неверные данные ключа — текст для администратора."""


def _hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def bearer(header):
    """Ключ из заголовка Authorization: Bearer puls_… или None."""
    if not isinstance(header, str) or not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    return token if KEY_RE.match(token) else None


def normalize_scopes(scopes):
    """Права в порядке каталога; `projects.stats` без `projects.read` смысла не имеет — read добавляется сам."""
    wanted = {s for s in scopes or [] if isinstance(s, str)}
    unknown = wanted - set(SCOPES)
    if unknown:
        raise IntegrationKeyError(f"Неизвестные права: {', '.join(sorted(unknown))}")
    if "projects.stats" in wanted:
        wanted.add("projects.read")
    if not wanted:
        raise IntegrationKeyError("Выберите хотя бы одно право")
    return [s for s in SCOPES if s in wanted]


def validate(name, app, scopes):
    name = " ".join(str(name or "").split())[:100]
    if not name:
        raise IntegrationKeyError("Укажите название ключа — например, «Дашборды компании»")
    if app not in APPS:
        raise IntegrationKeyError("Неизвестная система")
    return name, app, normalize_scopes(scopes)


def _secret():
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, _hash(key), key[:12]


def active(app=None):
    stmt = select(IntegrationKey).where(IntegrationKey.revoked_at.is_(None))
    if app:
        stmt = stmt.where(IntegrationKey.app == app)
    return db.session.execute(stmt.order_by(IntegrationKey.issued_at.desc(), IntegrationKey.id.desc())).scalars().all()


def issue(name, app, scopes, user):
    """Новый ключ: (ключ — показать один раз, запись)."""
    name, app, scopes = validate(name, app, scopes)
    if len(active()) >= MAX_ACTIVE:
        raise IntegrationKeyError(f"Действующих ключей не может быть больше {MAX_ACTIVE} — отзовите ненужные")
    key, digest, prefix = _secret()
    now = utcnow()
    row = IntegrationKey(name=name, app=app, scopes=scopes, key_hash=digest, prefix=prefix, issued_at=now,
                         issued_by_id=user.id if user else None, created_at=now)
    db.session.add(row)
    db.session.flush()
    audit.record(SECTION, "create", f"Ключ «{name}»", "key", "", f"{prefix}… · {', '.join(scopes)}", user=user)
    return key, row


def reissue(row, user):
    """Новый ключ для той же записи (название, система и права те же); прежний перестаёт действовать сразу."""
    if row.revoked_at is not None:
        raise IntegrationKeyError("Ключ отозван — выпустите новый")
    key, digest, prefix = _secret()
    old = row.prefix
    row.key_hash, row.prefix, row.issued_at = digest, prefix, utcnow()
    row.issued_by_id, row.last_used_at = user.id if user else None, None
    audit.record(SECTION, "update", f"Ключ «{row.name}»", "key", f"{old}…", f"{prefix}…", user=user)
    return key


def update(row, user, name=None, scopes=None):
    """Название и права ключа; ключ при этом не меняется."""
    if row.revoked_at is not None:
        raise IntegrationKeyError("Ключ отозван — его нельзя изменить")
    if name is not None:
        new_name, _app, _scopes = validate(name, row.app, row.scopes)
        if new_name != row.name:
            audit.record(SECTION, "update", f"Ключ «{new_name}»", "name", row.name, new_name, user=user)
            row.name = new_name
    if scopes is not None:
        new_scopes = normalize_scopes(scopes)
        if new_scopes != list(row.scopes or []):
            audit.record(SECTION, "update", f"Ключ «{row.name}»", "scopes", ", ".join(row.scopes or []),
                         ", ".join(new_scopes), user=user)
            row.scopes = new_scopes


def revoke(row, user):
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoked_by_id = user.id if user else None
        audit.record(SECTION, "delete", f"Ключ «{row.name}»", "key", f"{row.prefix}…", "", user=user)


def find(token):
    """Действующий ключ по предъявленному значению или None (без отметки использования)."""
    if not token:
        return None
    digest = _hash(token)
    row = db.session.execute(select(IntegrationKey).where(
        IntegrationKey.key_hash == digest, IntegrationKey.revoked_at.is_(None))).scalar_one_or_none()
    if row is None or not hmac.compare_digest(row.key_hash, digest):
        return None
    return row


def touch(row):
    """Отметка «последнее использование» — не чаще раза в минуту: каждый запрос не пишет в базу."""
    now = utcnow()
    if row.last_used_at is None or now - row.last_used_at >= LAST_USED_EVERY:
        row.last_used_at = now
        db.session.commit()


def has_scope(row, scope):
    return scope in (row.scopes or [])


def _iso(value):
    return value.replace(microsecond=0).isoformat() + "Z" if value else None


def serialize(row, users=None):
    users = users if users is not None else {}
    return {
        "id": row.id, "name": row.name, "app": row.app, "appLabel": APPS.get(row.app, row.app),
        "scopes": list(row.scopes or []), "prefix": row.prefix,
        "issuedAt": _iso(row.issued_at), "issuedBy": users.get(row.issued_by_id),
        "lastUsedAt": _iso(row.last_used_at), "revokedAt": _iso(row.revoked_at),
        "revokedBy": users.get(row.revoked_by_id), "active": row.revoked_at is None,
    }


def serialize_all(rows):
    ids = {i for r in rows for i in (r.issued_by_id, r.revoked_by_id) if i}
    users = {u.id: u.name for u in db.session.execute(select(User).where(User.id.in_(ids))).scalars()} if ids else {}
    return [serialize(r, users) for r in rows]
