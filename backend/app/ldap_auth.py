"""Вход по учётной записи каталога (LDAP / Active Directory).

Настройки хранятся в app_settings['ldap'] и меняются администратором в интерфейсе
(как в 1.0.x). Режимы:

* local  — только пароли системы;
* ldap   — только каталог; локальные администраторы сохраняют вход по паролю системы;
* hybrid — сначала каталог, затем пароль системы.

Сотрудник каталога при первом входе создаётся с ролью по умолчанию, при следующих
входах обновляются ФИО и email.
"""

from dataclasses import dataclass

from app.extensions import db
from app.models import AppSetting

DEFAULT_LDAP_CONFIG = {
    "enabled": False,
    "serverUrl": "",
    "baseDN": "",
    "bindDN": "",
    "bindPassword": "",
    "searchFilter": "(uid={{username}})",
    "userAttributes": {"username": "uid", "name": "cn", "email": "mail"},
    "authMode": "local",
    "defaultRole": "viewer",
    "timeoutSeconds": 5,
}


def get_ldap_config():
    row = db.session.get(AppSetting, "ldap")
    config = {**DEFAULT_LDAP_CONFIG, "userAttributes": dict(DEFAULT_LDAP_CONFIG["userAttributes"])}
    if row is not None and isinstance(row.value, dict):
        stored = row.value
        for key in DEFAULT_LDAP_CONFIG:
            if key in stored:
                config[key] = stored[key]
        attrs = stored.get("userAttributes") or {}
        config["userAttributes"] = {**DEFAULT_LDAP_CONFIG["userAttributes"], **{k: v for k, v in attrs.items() if v}}
    return config


def effective_mode(config=None):
    config = config or get_ldap_config()
    return config["authMode"] if config.get("enabled") else "local"


@dataclass
class DirectoryResult:
    ok: bool
    username: str = ""
    name: str = ""
    email: str = ""
    error: str = ""


def _server(config):
    from ldap3 import Server

    timeout = int(config.get("timeoutSeconds") or 5)
    return Server(config["serverUrl"], connect_timeout=timeout, get_info=None)


def _friendly(exc, unreachable_text):
    text = str(exc)
    if "invalidCredentials" in text or "49" in text:
        return "Неверные учётные данные"
    if "socket" in text.lower() or "connect" in text.lower() or "timed out" in text.lower():
        return unreachable_text
    return text or unreachable_text


def test_connection(config):
    from ldap3 import Connection

    if not config.get("serverUrl"):
        return {"ok": False, "error": "Адрес сервера не указан"}
    if not config.get("bindDN"):
        return {"ok": False, "error": "Bind DN не указан"}
    try:
        conn = Connection(_server(config), user=config["bindDN"], password=config.get("bindPassword", ""),
                          receive_timeout=int(config.get("timeoutSeconds") or 5))
        if not conn.bind():
            return {"ok": False, "error": "Неверные учётные данные Bind DN"}
        conn.unbind()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 — сообщение уходит администратору как есть
        return {"ok": False, "error": _friendly(exc, "Не удалось подключиться к серверу")}


def authenticate(username, password, config=None):
    from ldap3 import SUBTREE, Connection
    from ldap3.utils.conv import escape_filter_chars

    config = config or get_ldap_config()
    if not config.get("enabled"):
        return DirectoryResult(False, error="LDAP не включён")
    if not config.get("serverUrl") or not config.get("baseDN"):
        return DirectoryResult(False, error="LDAP не настроен")
    if not password:
        return DirectoryResult(False, error="Пустой пароль")
    attrs = config["userAttributes"]
    timeout = int(config.get("timeoutSeconds") or 5)
    try:
        service = Connection(_server(config), user=config.get("bindDN") or None,
                             password=config.get("bindPassword") or None, receive_timeout=timeout)
        if not service.bind():
            return DirectoryResult(False, error="Сервисная учётная запись каталога не принята")
        search_filter = config["searchFilter"].replace("{{username}}", escape_filter_chars(username))
        service.search(config["baseDN"], search_filter, search_scope=SUBTREE,
                       attributes=[a for a in {attrs["username"], attrs["name"], attrs["email"]} if a], size_limit=2)
        entries = list(service.entries)
        service.unbind()
        if not entries:
            return DirectoryResult(False, error="Пользователь не найден в каталоге")
        entry = entries[0]
        user_conn = Connection(_server(config), user=entry.entry_dn, password=password, receive_timeout=timeout)
        if not user_conn.bind():
            return DirectoryResult(False, error="Неверный пароль")
        user_conn.unbind()

        def attr(name):
            if not name or name not in entry.entry_attributes:
                return ""
            value = entry[name].value
            if isinstance(value, list):
                value = value[0] if value else ""
            return str(value or "")

        return DirectoryResult(True, username=attr(attrs["username"]) or username,
                               name=attr(attrs["name"]) or username, email=attr(attrs["email"]))
    except Exception as exc:  # noqa: BLE001
        return DirectoryResult(False, error=_friendly(exc, "Не удалось подключиться к LDAP-серверу"))
