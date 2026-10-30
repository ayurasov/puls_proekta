"""Профили конфигурации Flask.

Все секреты и адреса приходят из окружения (.env в Docker Compose). Значения по
умолчанию годятся только для разработки и тестов: профиль production отказывается
стартовать без длинного случайного SECRET_KEY.
"""

import os
from datetime import timedelta

from app.version import APP_VERSION as _PACKAGE_VERSION

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_SQLITE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'puls_dev.db')}"
_INSECURE_DEV_SECRET_KEY = "dev-secret-key-not-for-production"
_KNOWN_INSECURE_SECRET_KEYS = {_INSECURE_DEV_SECRET_KEY, "change-me", "change-me-in-production", ""}


def _bool(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", _INSECURE_DEV_SECRET_KEY)
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL") or DEFAULT_SQLITE_URI
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Серверные сессии в таблице user_sessions (ADR-0003): переживают перезапуск
    # контейнеров и отзываются при отключении сотрудника или смене пароля.
    SESSION_COOKIE_NAME = "puls_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(days=_int("SESSION_LIFETIME_DAYS", 30))
    # Как часто продлевать срок сессии при активности (чтобы не писать в БД на каждый запрос)
    SESSION_REFRESH_SECONDS = _int("SESSION_REFRESH_SECONDS", 600)

    WTF_CSRF_ENABLED = True
    WTF_CSRF_HEADERS = ["X-CSRF-Token"]
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT = False

    RATELIMIT_ENABLED = _bool("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    # Попытки входа: на одну учётную запись и (с запасом на офис за NAT) с одного адреса
    LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "10 per minute;50 per hour")
    LOGIN_IP_RATE_LIMIT = os.environ.get("LOGIN_IP_RATE_LIMIT", "300 per minute")
    # Сколько обратных прокси перед backend (nginx контейнера frontend — один; плюс HTTPS-прокси сервера — два):
    # адрес сотрудника берётся из X-Forwarded-For с учётом этого числа
    TRUSTED_PROXY_HOPS = _int("TRUSTED_PROXY_HOPS", 1)

    # Аватар — изображение до 2 МБ (как в 1.0.1); тело запроса ограничено с запасом,
    # импорт плана из JSON — до 20 МБ.
    AVATAR_MAX_BYTES = _int("AVATAR_MAX_BYTES", 2 * 1024 * 1024)
    MAX_CONTENT_LENGTH = _int("MAX_CONTENT_LENGTH", 25 * 1024 * 1024)

    # Автоматические снимки плана (ADR-0002): не чаще раза в N минут редактирования
    SNAPSHOT_INTERVAL_MINUTES = _int("SNAPSHOT_INTERVAL_MINUTES", 30)
    # Кто считается «на плане» в индикаторе присутствия
    PRESENCE_TTL_SECONDS = _int("PRESENCE_TTL_SECONDS", 45)

    # Задачи Jira (ADR-0007): сколько секунд данные задачи считаются свежими при открытии плана
    JIRA_CACHE_SECONDS = _int("JIRA_CACHE_SECONDS", 300)

    # Часовой пояс площадки: в нём выражены даты прежней версии (комментарии, история)
    # и «сегодня» в журналах.
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Moscow")

    APP_VERSION = os.environ.get("APP_VERSION") or _PACKAGE_VERSION

    # Первичный администратор пустой базы (новая установка без переноса данных)
    BOOTSTRAP_ADMIN_LOGIN = os.environ.get("BOOTSTRAP_ADMIN_LOGIN", "admin")
    BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    ENV = "development"


class TestingConfig(BaseConfig):
    TESTING = True
    ENV = "testing"
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    BCRYPT_ROUNDS = 4


class E2EConfig(BaseConfig):
    ENV = "e2e"
    RATELIMIT_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    ENV = "production"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    WTF_CSRF_SSL_STRICT = _bool("WTF_CSRF_SSL_STRICT", False)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_timeout": _int("DB_POOL_TIMEOUT", 5),
        "pool_size": _int("DB_POOL_SIZE", 5),
        "max_overflow": _int("DB_MAX_OVERFLOW", 5),
    }


def _require_production_settings():
    secret_key = os.environ.get("SECRET_KEY", "")
    if secret_key in _KNOWN_INSECURE_SECRET_KEYS or len(secret_key) < 32:
        raise RuntimeError("Production requires a random SECRET_KEY of at least 32 characters")


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "e2e": E2EConfig,
    "production": ProductionConfig,
}


def get_config(name=None):
    name = name or os.environ.get("FLASK_ENV", "development")
    if name == "production":
        _require_production_settings()
    return config_by_name.get(name, DevelopmentConfig)
