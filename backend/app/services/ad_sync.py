"""Синхронизация структуры компании с Active Directory — как в todo-planner «По Делу» (ADR-0044, этап 4).

Подключение — то же, что для входа через домен (Администрирование → Настройки → Вход через домен,
app_settings['ldap']: адрес сервера, Base DN, сервисная учётная запись). Параметры синхронизации
хранятся в app_settings['org_sync'] и меняются на странице «Импорт и экспорт»:

* userFilter — фильтр сотрудников (по умолчанию ``(&(objectCategory=person)(objectClass=user)(mail=*))``);
* pathSource — откуда путь подразделения: ``attributes`` (атрибуты сотрудника, по умолчанию
  company / division / department) или ``ou`` (организационные единицы в distinguishedName);
* pathAttributes — атрибуты пути от корня;
* detectHeads — руководитель подразделения: тот, кому подчиняются сотрудники его подразделения,
  а сам он подчиняется кому-то вне его (или никому).

Сотрудники каталога превращаются в строки импорта: руководитель — атрибут manager (DN), внешний
ключ — objectGUID, отключённая учётная запись (userAccountControl, бит 2) — отключённый сотрудник.
Дальше работает общий планировщик org_import: сначала просмотр изменений, затем применение одной
транзакцией.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from app.extensions import db
from app.ldap_auth import _friendly, _server, get_ldap_config
from app.models import AppSetting
from app.services.org_import import Row, clean

DEFAULT_FILTER = "(&(objectCategory=person)(objectClass=user)(mail=*))"
DEFAULT_PATH_ATTRIBUTES = ("company", "division", "department")
SETTING_KEY = "org_sync"
UAC_DISABLED = 0x2
ATTRIBUTES = (
    "objectGUID", "mail", "displayName", "cn", "sAMAccountName", "title", "manager",
    "userAccountControl", "distinguishedName",
)
DEFAULT_OPTIONS = {"userFilter": DEFAULT_FILTER, "pathSource": "attributes",
                   "pathAttributes": list(DEFAULT_PATH_ATTRIBUTES), "detectHeads": True}


class AdSyncError(RuntimeError):
    """Каталог не настроен, ldap3 не установлен или каталог недоступен."""


@dataclass
class AdConfig:
    url: str
    bind_dn: str
    bind_password: str
    base_dn: str
    user_filter: str = DEFAULT_FILTER
    path_source: str = "attributes"
    path_attributes: tuple = DEFAULT_PATH_ATTRIBUTES
    detect_heads: bool = True
    timeout: int = 10
    ldap: dict = field(default_factory=dict, repr=False)

    def describe(self):
        """Без секретов — для страницы «Импорт и экспорт»."""
        return {"url": self.url, "baseDn": self.base_dn, "userFilter": self.user_filter, "pathSource": self.path_source,
                "pathAttributes": list(self.path_attributes), "detectHeads": self.detect_heads}


def get_options():
    row = db.session.get(AppSetting, SETTING_KEY)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    options = {**DEFAULT_OPTIONS, **{k: v for k, v in stored.items() if k in DEFAULT_OPTIONS}}
    options["pathAttributes"] = list(options["pathAttributes"] or DEFAULT_PATH_ATTRIBUTES)
    return options


def save_options(options):
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is None:
        db.session.add(AppSetting(key=SETTING_KEY, value=options))
    else:
        row.value = options


def current_config():
    """Настройки подключения и синхронизации; None — каталог не настроен (нет адреса или Base DN)."""
    ldap = get_ldap_config()
    url, base_dn = (ldap.get("serverUrl") or "").strip(), (ldap.get("baseDN") or "").strip()
    if not url or not base_dn:
        return None
    options = get_options()
    return AdConfig(
        url=url, bind_dn=ldap.get("bindDN") or "", bind_password=ldap.get("bindPassword") or "", base_dn=base_dn,
        user_filter=options["userFilter"] or DEFAULT_FILTER,
        path_source="ou" if options["pathSource"] == "ou" else "attributes",
        path_attributes=tuple(options["pathAttributes"]), detect_heads=bool(options["detectHeads"]),
        timeout=max(5, int(ldap.get("timeoutSeconds") or 5)), ldap=ldap,
    )


def ldap3_available():
    try:
        import ldap3  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------- чтение каталога
def connect(config):
    """Подключение сервисной учётной записью (в тестах подменяется соединением ldap3 MOCK_SYNC)."""
    import ldap3

    return ldap3.Connection(_server({**config.ldap, "serverUrl": config.url, "timeoutSeconds": config.timeout}),
                            user=config.bind_dn or None, password=config.bind_password or None, auto_bind=True,
                            receive_timeout=max(30, config.timeout), read_only=True)


def fetch_entries(config):
    """Записи пользователей: [{"dn": str, "attributes": {name: value}}]."""
    if not ldap3_available():
        raise AdSyncError("Пакет ldap3 не установлен на сервере")
    try:
        connection = connect(config)
    except Exception as error:  # noqa: BLE001 — ldap3 бросает разные исключения подключения
        raise AdSyncError(f"Не удалось подключиться к каталогу: {_friendly(error, 'сервер недоступен')}") from error
    attributes = list(dict.fromkeys([*ATTRIBUTES, *config.path_attributes]))
    try:
        entries = []
        generator = connection.extend.standard.paged_search(
            config.base_dn, config.user_filter, attributes=attributes, paged_size=500, generator=True,
        )
        for item in generator:
            if item.get("type") != "searchResEntry":
                continue
            attrs = dict(item.get("attributes") or {})
            raw_guid = (item.get("raw_attributes") or {}).get("objectGUID")
            if raw_guid and isinstance(raw_guid[0], bytes) and len(raw_guid[0]) == 16:
                attrs["objectGUID"] = raw_guid[0]   # без схемы каталога ldap3 может отдать GUID строкой
            entries.append({"dn": item.get("dn", ""), "attributes": attrs})
        return entries
    except Exception as error:  # noqa: BLE001
        raise AdSyncError(f"Не удалось прочитать каталог: {_friendly(error, 'сервер недоступен')}") from error
    finally:
        try:
            connection.unbind()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- преобразование
_GUID = re.compile(r"^\{?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}?$")


def _first(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        try:
            return str(uuid.UUID(bytes_le=value)) if len(value) == 16 else value.decode("utf-8", "ignore")
        except ValueError:
            return value.hex()
    text = "" if value is None else str(value).strip()
    match = _GUID.match(text)
    return match.group(1).lower() if match else text


_OU = re.compile(r"(?i)^OU=(.+)$")


def _split_dn(dn):
    """DN → компоненты с учётом экранированных запятых."""
    parts, current, escaped = [], "", False
    for char in dn or "":
        if escaped:
            current += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _path_from_ou(dn, base_dn):
    base = [part.lower() for part in _split_dn(base_dn)]
    parts = _split_dn(dn)[1:]   # без CN самого пользователя
    if base and [p.lower() for p in parts[-len(base):]] == base:
        parts = parts[: len(parts) - len(base)]
    names = [match.group(1) for part in parts if (match := _OU.match(part))]
    return list(reversed(names))


def _uac_disabled(value):
    try:
        return bool(int(_first(value) or 0) & UAC_DISABLED)
    except ValueError:
        return False


def entries_to_rows(entries, config):
    """Записи каталога → (строки импорта, словарь DN → objectGUID для ссылок на руководителя)."""
    rows, refs = [], {}
    for index, entry in enumerate(entries, start=1):
        attrs = {key.lower(): value for key, value in (entry.get("attributes") or {}).items()}
        dn = entry.get("dn") or _first(attrs.get("distinguishedname"))
        guid = _first(attrs.get("objectguid")) or dn
        refs[dn.lower()] = guid
        if config.path_source == "ou":
            path = _path_from_ou(dn, config.base_dn)
        else:
            path = [_first(attrs.get(name.lower())) for name in config.path_attributes]
        path = [clean(part)[:200] for part in path if clean(part)]
        rows.append(Row(
            line=index,
            email=_first(attrs.get("mail")).lower(),
            name=clean(_first(attrs.get("displayname")) or _first(attrs.get("cn")))[:255],
            login=_first(attrs.get("samaccountname")),
            department=path,
            position=clean(_first(attrs.get("title")))[:500] or None,
            manager_ref=_first(attrs.get("manager")).lower(),
            external_id=guid,
            disabled=_uac_disabled(attrs.get("useraccountcontrol")),
        ))
    if config.detect_heads:
        _mark_heads(rows, refs)
    return rows, refs


def _mark_heads(rows, refs):
    """Руководитель подразделения — у кого есть подчинённые в том же подразделении,
    а его собственный руководитель — вне этого подразделения (или его нет)."""
    by_guid = {row.external_id: row for row in rows}
    has_local_reports = set()
    for row in rows:
        manager = by_guid.get(refs.get(row.manager_ref, "")) if row.manager_ref else None
        if manager is not None and manager.department == row.department and manager is not row:
            has_local_reports.add(manager.line)
    for row in rows:
        manager = by_guid.get(refs.get(row.manager_ref, "")) if row.manager_ref else None
        managed_outside = manager is None or manager.department != row.department
        if row.line in has_local_reports and managed_outside and not row.disabled:
            row.is_head = True


def load_rows(config=None):
    config = config or current_config()
    if config is None:
        raise AdSyncError("Каталог не настроен: укажите адрес сервера и Base DN в разделе «Настройки → Вход через домен»")
    return entries_to_rows(fetch_entries(config), config)
