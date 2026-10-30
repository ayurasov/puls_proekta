"""Расширения Flask в одном месте."""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine

# Стабильные имена ограничений: Alembic-миграции одинаковы в SQLite и PostgreSQL
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


@event.listens_for(Engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """SQLite (разработка, тесты, e2e): внешние ключи, WAL и ожидание блокировки.

    WAL позволяет читать во время записи: сессия сохраняется отдельным соединением,
    пока транзакция запроса ещё открыта на чтение (в PostgreSQL это не нужно).
    """
    if dbapi_connection.__class__.__module__.split(".")[0] != "sqlite3":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))
csrf = CSRFProtect()
# Лимиты навешиваются точечно (вход), ключ — IP клиента (ProxyFix учитывает X-Forwarded-For)
limiter = Limiter(key_func=get_remote_address)
