"""Подключение к «По Делу» (ADR-0009): хранение в app_settings['podelu'], пароль — зашифрованным.

Как у Jira (ADR-0007): одно подключение на систему, форма — у администратора, пароль в интерфейс не
возвращается и уходит только на тот адрес, для которого был введён. Сотрудникам доступно одно: есть ли
подключение и адрес «По Делу» — для ссылок на подразделения и карточки сотрудников.
"""

from flask import current_app

from app.extensions import db
from app.models import AppSetting
from app.podelu_client import PodeluClient, PodeluError, check_base_url, decrypt_password

SETTING_KEY = "podelu"
DEFAULT_CONNECTION = {"baseUrl": "", "login": "", "passwordEncrypted": "", "acceptInvalidCerts": False}
NOT_CONFIGURED = "Подключение к «По Делу» не настроено — укажите его в разделе «Администрирование → Интеграции»"


def get_connection():
    """Сохранённое подключение или None."""
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is None or not isinstance(row.value, dict) or not row.value.get("baseUrl"):
        return None
    return {**DEFAULT_CONNECTION, **{k: v for k, v in row.value.items() if k in DEFAULT_CONNECTION}}


def save_connection(data):
    value = {key: data.get(key, DEFAULT_CONNECTION[key]) for key in DEFAULT_CONNECTION}
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is None:
        db.session.add(AppSetting(key=SETTING_KEY, value=value))
    else:
        row.value = value


def delete_connection():
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is not None:
        db.session.delete(row)


def admin_json(conn):
    """Подключение для формы администратора — без пароля."""
    if not conn:
        return None
    return {"baseUrl": conn["baseUrl"], "login": conn["login"] or "", "acceptInvalidCerts": bool(conn["acceptInvalidCerts"]),
            "hasPassword": bool(conn["passwordEncrypted"])}


def public_json(conn):
    """Что видят все сотрудники: есть ли подключение и адрес «По Делу» (для ссылок), без учётной записи."""
    if not conn:
        return {"connected": False, "baseUrl": None}
    return {"connected": True, "baseUrl": conn["baseUrl"]}


def make_client(override=None, total_timeout=None):
    """Клиент по сохранённому подключению; override — данные формы администратора (проверка до сохранения).
    Без нового пароля в форме используется сохранённый — только для того же адреса и логина. total_timeout — предел
    всей операции, по умолчанию PODELU_TOTAL_TIMEOUT (запрос к «Пульсу» не дольше таймаута gunicorn)."""
    saved = get_connection()
    if override is None:
        if not saved:
            raise PodeluError(NOT_CONFIGURED)
        conf = saved
        password = decrypt_password(current_app.secret_key, saved["passwordEncrypted"]) \
            if saved["passwordEncrypted"] else ""
    else:
        conf = override
        password = override.get("password") or ""
        if not password:
            same = saved and saved["passwordEncrypted"] and check_base_url(override.get("baseUrl")) == saved["baseUrl"] \
                and (override.get("login") or "") == saved["login"]
            if not same:
                raise PodeluError("Укажите пароль учётной записи «По Делу»")
            if override.get("acceptInvalidCerts") and not saved["acceptInvalidCerts"]:
                raise PodeluError("Без проверки сертификата введите пароль заново")
            password = decrypt_password(current_app.secret_key, saved["passwordEncrypted"])
    if not conf.get("login"):
        raise PodeluError("Укажите логин учётной записи «По Делу»")
    if not password:
        raise PodeluError("Укажите пароль учётной записи «По Делу»")
    return PodeluClient(conf["baseUrl"], conf["login"], password, accept_invalid_certs=bool(conf.get("acceptInvalidCerts")),
                        total_timeout=total_timeout or current_app.config.get("PODELU_TOTAL_TIMEOUT") or None)
