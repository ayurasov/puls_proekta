"""Серверные сессии в таблице user_sessions (ADR-0003).

В cookie лежит только случайный токен (256 бит); в БД — его SHA-256, данные сессии и
срок действия. Плюсы по сравнению с файловыми сессиями и cookie-сессиями 1.0.x:

* сессии переживают пересоздание контейнеров и работают с несколькими процессами gunicorn;
* отключение сотрудника, смена или сброс пароля удаляют все его сессии — доступ
  пропадает сразу, а не через год (так было в 1.0.x: cookie жила 365 дней);
* утечка дампа БД не раскрывает действующие токены.

Запись в БД идёт отдельным коротким соединением, независимо от транзакции запроса:
ошибка обработчика и откат его изменений не теряют сессию, а сохранение сессии
не фиксирует чужие незавершённые изменения.
"""

import hashlib
import secrets
from datetime import timedelta

from flask.sessions import SessionInterface, SessionMixin
from sqlalchemy import delete, insert, select, update
from werkzeug.datastructures import CallbackDict

from app.extensions import db
from app.models import UserSession, utcnow



ANONYMOUS_LIFETIME = timedelta(days=1)

def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DbSession(CallbackDict, SessionMixin):
    def __init__(self, initial=None, token=None, is_new=False, expires_at=None):
        def on_update(_self):
            _self.modified = True

        super().__init__(initial or {}, on_update)
        self.token = token
        self.new = is_new
        self.modified = False
        self.expires_at = expires_at
        self.regenerate_requested = False
        self.destroy_requested = False

    def regenerate(self):
        """Новый идентификатор при входе: идентификатор, известный до входа, не переиспользуется."""
        self.regenerate_requested = True
        self.modified = True

    def destroy(self):
        self.clear()
        self.destroy_requested = True
        self.modified = True


class DbSessionInterface(SessionInterface):
    def open_session(self, app, request):
        token = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        if token and len(token) <= 128:
            with db.engine.connect() as conn:
                row = conn.execute(
                    select(UserSession.data, UserSession.expires_at, UserSession.last_seen_at)
                    .where(UserSession.id == _hash(token))
                ).first()
            if row is not None and row.expires_at > utcnow():
                session = DbSession(dict(row.data or {}), token=token, expires_at=row.expires_at)
                session.last_seen_at = row.last_seen_at
                return session
        return DbSession(is_new=True)

    def _cookie_kwargs(self, app):
        return {
            "domain": self.get_cookie_domain(app),
            "path": self.get_cookie_path(app),
            "httponly": self.get_cookie_httponly(app),
            "secure": self.get_cookie_secure(app),
            "samesite": self.get_cookie_samesite(app),
        }

    def save_session(self, app, session, response):
        name = app.config["SESSION_COOKIE_NAME"]
        lifetime = app.permanent_session_lifetime
        now = utcnow()

        if session.destroy_requested or (not session and session.modified and session.token):
            if session.token:
                with db.engine.begin() as conn:
                    conn.execute(delete(UserSession).where(UserSession.id == _hash(session.token)))
            response.delete_cookie(name, **{k: v for k, v in self._cookie_kwargs(app).items() if k in ("domain", "path")})
            return

        if not session:
            return

        old_token = session.token
        rotate = session.new or session.regenerate_requested
        refresh_after = timedelta(seconds=app.config.get("SESSION_REFRESH_SECONDS", 600))
        needs_touch = (not rotate and getattr(session, "last_seen_at", None) is not None
                       and now - session.last_seen_at > refresh_after)

        if not (rotate or session.modified or needs_touch):
            return

        payload = dict(session)
        user_id = payload.get("user_id")
        if user_id is None:
            # Сессия без входа нужна только для CSRF-токена страницы входа: живёт сутки, а не месяц,
            # иначе запросы без cookie копили бы в таблице записи на 30 дней
            lifetime = ANONYMOUS_LIFETIME
        expires_at = now + lifetime
        from flask import request

        with db.engine.begin() as conn:
            if rotate:
                if old_token:
                    conn.execute(delete(UserSession).where(UserSession.id == _hash(old_token)))
                token = secrets.token_urlsafe(32)
                conn.execute(insert(UserSession).values(
                    id=_hash(token), user_id=user_id, data=payload, created_at=now, last_seen_at=now,
                    expires_at=expires_at, ip=(request.remote_addr or "")[:64],
                    user_agent=(request.headers.get("User-Agent") or "")[:300],
                ))
                session.token = token
                if user_id is None:
                    conn.execute(delete(UserSession).where(UserSession.user_id.is_(None), UserSession.expires_at < now))
            else:
                values = {"last_seen_at": now, "expires_at": expires_at}
                if session.modified:
                    values.update({"data": payload, "user_id": user_id})
                conn.execute(update(UserSession).where(UserSession.id == _hash(old_token)).values(**values))

        response.set_cookie(name, session.token, max_age=int(lifetime.total_seconds()), **self._cookie_kwargs(app))


def revoke_user_sessions(user_id, keep_token=None):
    """Удалить все сессии сотрудника (кроме текущей, если она указана)."""
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token:
        stmt = stmt.where(UserSession.id != _hash(keep_token))
    with db.engine.begin() as conn:
        conn.execute(stmt)


def purge_expired_sessions():
    with db.engine.begin() as conn:
        conn.execute(delete(UserSession).where(UserSession.expires_at <= utcnow()))
