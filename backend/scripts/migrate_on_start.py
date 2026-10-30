"""Миграции схемы перед запуском приложения (entrypoint контейнера backend).

1. ждёт доступности БД;
2. берёт advisory-блокировку PostgreSQL — одновременно мигрирует только один процесс;
3. применяет `alembic upgrade head` и проверяет, что схема на последней ревизии;
4. в пустой базе создаёт администратора из BOOTSTRAP_ADMIN_PASSWORD (если задан).

Миграции 0.x только добавляют таблицы и поля (additive); данные не удаляются.
"""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
MIGRATION_LOCK_KEY = 0x70756C7370726F6A  # "pulsproj"


def _positive_int(name, default):
    value = int(os.environ.get(name, default))
    if value < 1:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def database_url():
    from config import DEFAULT_SQLITE_URI

    return os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL") or DEFAULT_SQLITE_URI


def wait_for_database(engine):
    attempts = _positive_int("DB_MIGRATION_CONNECT_ATTEMPTS", 60)
    delay = _positive_int("DB_MIGRATION_RETRY_SECONDS", 1)
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except SQLAlchemyError as exc:
            if attempt == attempts:
                raise RuntimeError(f"Database unavailable after {attempts} attempts") from exc
            print(f"Database unavailable ({attempt}/{attempts}); retrying in {delay}s", flush=True)
            time.sleep(delay)


@contextmanager
def migration_lock(engine):
    if engine.dialect.name != "postgresql":
        yield
        return
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        yield
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.close()


def alembic_config(url):
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    return config


def verify_at_head(engine, config):
    expected = set(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    if current != expected:
        raise RuntimeError(f"Alembic revision mismatch: current={sorted(current)}, expected={sorted(expected)}")
    print(f"Database schema is at Alembic head: {', '.join(sorted(current))}", flush=True)


def main():
    url = database_url()
    engine = create_engine(url, pool_pre_ping=True)
    try:
        wait_for_database(engine)
        config = alembic_config(url)
        with migration_lock(engine):
            command.upgrade(config, "head")
            verify_at_head(engine, config)
    finally:
        engine.dispose()

    from app import create_app
    from app.cli import ensure_bootstrap_admin

    ensure_bootstrap_admin(create_app(os.environ.get("FLASK_ENV", "production")))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Automatic migration failed: {exc}", file=sys.stderr)
        raise
