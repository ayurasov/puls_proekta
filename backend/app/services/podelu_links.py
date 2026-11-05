"""Ссылки из задач «По Делу» на проекты «Пульса» — ответная интеграция (ADR-0010).

Обмен ключами, как у вебхуков: каждая система выпускает свой ключ, администратор вводит его в другую систему.

* Ключ «Пульса» (``puls_…``) выпускается здесь; «По Делу» читает с ним проекты и строки планов
  (``/api/integration/v1``). С 0.6.0 это обычный ключ интеграции (ADR-0011, таблица integration_keys) системы
  «podelu» с правом projects.read; страница «По Делу» управляет им так же, как раньше.
* Ключ «По Делу» (``podelu_…``) выпускается в «По Делу»; «Пульс» читает с ним задачи, связанные с проектом
  (``/api/integrations/puls``). Хранится зашифрованным (Fernet, ключ из SECRET_KEY со своей солью), в интерфейс не
  возвращается и уходит только на адрес, для которого введён.

Настройки — app_settings['podelu_links']. Показ задач «По Делу» в «Пульсе» включает администратор — только
после успешной проверки связи с тем же адресом и ключом.
"""

import base64
import hashlib
import re
from datetime import datetime, timezone

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from app.extensions import db
from app.models import AppSetting
from app.podelu_client import PodeluError, check_base_url, check_server_url, connection_error
from app.services import integration_keys

SETTING_KEY = "podelu_links"
DEFAULTS = {
    # serverUrl (0.5.1) — адрес для запросов сервера «Пульса», если «По Делу» видна ему по другому адресу (оба
    # сервиса в Docker на одной машине); пусто — запросы идут на baseUrl. Ссылки на задачи — всегда от baseUrl
    "baseUrl": "", "serverUrl": "", "keyEncrypted": "", "acceptInvalidCerts": False, "enabled": False,
    "checkedAt": None, "checkedVersion": "", "checkedFingerprint": "",
}
OUTBOUND_PREFIX = "podelu_"
KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")
TIMEOUT = (5, 15)
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
NOT_CONFIGURED = "Связь с «По Делу» для ссылок из задач не настроена"


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_settings():
    row = db.session.get(AppSetting, SETTING_KEY)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    return {**DEFAULTS, **{k: v for k, v in value.items() if k in DEFAULTS}}


def save_settings(data):
    value = {key: data.get(key, DEFAULTS[key]) for key in DEFAULTS}
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is None:
        db.session.add(AppSetting(key=SETTING_KEY, value=value))
    else:
        row.value = value


def delete_settings():
    row = db.session.get(AppSetting, SETTING_KEY)
    if row is not None:
        db.session.delete(row)


# ---------- ключ «Пульса» для «По Делу» (ключ интеграции системы podelu, ADR-0011) ----------

def inbound_key():
    """Действующий ключ «Пульса» для «По Делу» (последний выпущенный) или None."""
    keys = integration_keys.active("podelu")
    return keys[0] if keys else None


def issue_inbound_key(user=None):
    """Ключ «Пульса» для «По Делу»: перевыпуск действующего или новый с правом projects.read.
    (ключ — показать один раз, сведения для показа). Прежний ключ перестаёт действовать сразу."""
    row = inbound_key()
    if row is None:
        key, row = integration_keys.issue("По Делу", "podelu", ["projects.read"], user)
    else:
        key = integration_keys.reissue(row, user)
    return key, {"prefix": row.prefix, "createdAt": _iso(row.issued_at)}


def revoke_inbound_key(user=None):
    for row in integration_keys.active("podelu"):
        integration_keys.revoke(row, user)


def _iso(value):
    return value.replace(microsecond=0).isoformat() + "Z" if value else None


# ---------- ключ «По Делу» для «Пульса» ----------

def _fernet(secret_key):
    # Своя соль: зашифрованное значение другой интеграции (Jira, пароль «По Делу») сюда не подходит
    key = base64.urlsafe_b64encode(hashlib.sha256(("podelu-links-key:" + str(secret_key)).encode()).digest())
    return Fernet(key)


def encrypt_key(key):
    return _fernet(current_app.secret_key).encrypt(key.encode()).decode()


def decrypt_key(encrypted):
    try:
        return _fernet(current_app.secret_key).decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError):
        raise PodeluError("Сохранённый ключ «По Делу» не удаётся расшифровать (сменился SECRET_KEY сервера) — "
                          "введите ключ заново") from None


def fingerprint(base_url, key, server_url=""):
    """Отпечаток «адрес + ключ» (и адреса для запросов сервера): проверка связи действительна, пока они не менялись."""
    material = f"{base_url}\n{key}" + (f"\n{server_url}" if server_url else "")
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def check_key(key):
    key = (key or "").strip()
    if not key.startswith(OUTBOUND_PREFIX) or not KEY_RE.match(key):
        raise PodeluError("Ключ «По Делу» начинается с «podelu_» — скопируйте его целиком в «По Делу»: "
                          "Настройки → Интеграции → «Пульс проекта» → «Ключ для «Пульса»»")
    return key


def ready(settings=None):
    """Показ задач «По Делу» включён и проверен для сохранённых адреса и ключа."""
    settings = settings or get_settings()
    return bool(settings["enabled"] and settings["baseUrl"] and settings["keyEncrypted"] and settings["checkedAt"])


def admin_json(settings=None, suggested_url=""):
    settings = settings or get_settings()
    key = inbound_key()
    inbound = {"prefix": key.prefix, "createdAt": _iso(key.issued_at), "lastUsedAt": _iso(key.last_used_at)} \
        if key else None
    return {
        "baseUrl": settings["baseUrl"], "serverUrl": settings.get("serverUrl") or "",
        "acceptInvalidCerts": bool(settings["acceptInvalidCerts"]),
        "hasKey": bool(settings["keyEncrypted"]), "enabled": bool(settings["enabled"]),
        "checkedAt": settings["checkedAt"], "checkedVersion": settings["checkedVersion"] or "",
        "inbound": inbound,
        "suggestedBaseUrl": suggested_url,
    }


# ---------- клиент «По Делу» ----------

class PodeluLinksClient:
    """Чтение задач «По Делу», связанных с проектами «Пульса»: GET с ключом, без cookie и перенаправлений."""

    def __init__(self, base_url, key, accept_invalid_certs=False, timeout=TIMEOUT, server_url=""):
        # base_url — адрес для сотрудников (от него ссылки на задачи); запросы — на адрес для запросов сервера
        self.base_url = check_base_url(base_url)
        self.server_url = check_server_url(server_url, self.base_url)
        self.request_url = self.server_url or self.base_url
        self.key = check_key(key)
        self.timeout = timeout
        # on_result(ok, message) — итог каждого запроса для «Состояния системы» (ADR-0014)
        self.on_result = None
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Authorization": f"Bearer {self.key}"})
        self.session.verify = not accept_invalid_certs
        if accept_invalid_certs:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def get(self, path, params=None):
        """Ответ «По Делу»; сбой связи, отказ в ключе и ошибки сервера сообщаются в on_result как неполадка."""
        try:
            data = self._get(path, params)
        except PodeluError as exc:
            bad = exc.status is None or exc.status in (401, 403) or exc.status >= 500
            self._report(not bad, str(exc) if bad else "")
            raise
        self._report(True, "")
        return data

    def _report(self, ok, message):
        if self.on_result is not None:
            try:
                self.on_result(ok, message)
            except Exception:  # noqa: BLE001 — учёт состояния не должен ломать запрос
                pass

    def _get(self, path, params=None):
        url = f"{self.request_url}/api/integrations/puls/{path.lstrip('/')}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout, allow_redirects=False, stream=True,
                                    verify=self.session.verify)
        except requests.exceptions.SSLError:
            raise PodeluError("Ошибка TLS-сертификата «По Делу» — для самоподписанного сертификата включите «Не "
                              "проверять TLS-сертификат»") from None
        except requests.exceptions.Timeout:
            raise PodeluError(f"«По Делу» ({self.request_url}) не ответила вовремя — проверьте межсетевой экран и прокси "
                              "сервера «Пульса» (NO_PROXY для внутренних адресов)") from None
        except requests.exceptions.ConnectionError as exc:
            raise connection_error(exc, self.request_url, bool(self.server_url)) from None
        except (requests.RequestException, ValueError):
            raise PodeluError("Не удалось выполнить запрос к «По Делу»") from None
        try:
            if resp.is_redirect:
                raise PodeluError(f"«По Делу» перенаправляет запрос ({resp.status_code}) — укажите итоговый адрес "
                                  "(например, с https://)", resp.status_code)
            body = resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        finally:
            resp.close()
        if len(body) > MAX_RESPONSE_BYTES:
            raise PodeluError("Ответ «По Делу» слишком большой")
        try:
            import json

            data = json.loads(body.decode("utf-8")) if body else None
        except (ValueError, UnicodeDecodeError):
            data = None
        if resp.status_code == 401:
            raise PodeluError("«По Делу» отклонила ключ (401) — выпустите ключ для «Пульса» в «По Делу» заново и "
                              "введите его здесь", 401)
        if resp.status_code == 403:
            message = data.get("message") if isinstance(data, dict) else ""
            raise PodeluError(f"«По Делу» не разрешает запрос (403){': ' + message if message else ''} — включите ссылки "
                              "на проекты в «По Делу»: Настройки → Интеграции → «Пульс проекта»", 403)
        if resp.status_code == 404:
            raise PodeluError("По этому адресу нет «По Делу» 1.4.1 или новее (404) — проверьте адрес; ответная "
                              "интеграция появилась в «По Делу» 1.4.1", 404)
        if resp.status_code != 200 or not isinstance(data, dict):
            message = data.get("message") if isinstance(data, dict) else ""
            raise PodeluError(f"«По Делу» вернула ошибку {resp.status_code}{': ' + message if message else ''}",
                              resp.status_code)
        return data

    def ping(self):
        data = self.get("ping")
        if data.get("app") != "podelu":
            raise PodeluError("По этому адресу отвечает не «По Делу»")
        return {"name": str(data.get("name") or "По Делу")[:100], "version": str(data.get("version") or "")[:30],
                "linksEnabled": bool(data.get("linksEnabled"))}

    def project_tasks(self, project_id, item=None, limit=200):
        params = {"limit": limit}
        if item:
            params["item"] = item
        data = self.get(f"projects/{int(project_id)}/tasks", params)
        return [normalize_task(t, self.base_url) for t in data.get("tasks") or [] if isinstance(t, dict)], \
            bool(data.get("truncated"))

    def summary(self, project_ids):
        if not project_ids:
            return {}
        data = self.get("summary", {"projects": ",".join(str(int(p)) for p in project_ids[:500])})
        counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
        out = {}
        for key, value in counts.items():
            if str(key).isdigit() and isinstance(value, dict):
                out[int(key)] = {"total": int(value.get("total") or 0), "open": int(value.get("open") or 0)}
        return out


def _text(value, limit):
    return str(value)[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _day(value):
    return value[:10] if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value) else None


def normalize_task(task, base_url):
    """Задача «По Делу» из ответа — только ожидаемые поля и длины; ссылка — только на адрес «По Делу»."""
    path = task.get("path") if isinstance(task.get("path"), str) else ""
    url = f"{base_url}{path}" if path.startswith("/") and not path.startswith("//") else None
    assignee = task.get("assignee") if isinstance(task.get("assignee"), dict) else None
    plan_id = task.get("planId")
    item_key = task.get("itemKey")
    return {
        "id": _text(task.get("id"), 64),
        "title": _text(task.get("title"), 500),
        "status": _text(task.get("status"), 40),
        "statusLabel": _text(task.get("statusLabel"), 60) or _text(task.get("status"), 40),
        "done": bool(task.get("done")),
        "priority": _text(task.get("priority"), 40),
        "priorityLabel": _text(task.get("priorityLabel"), 60),
        "assignee": {"name": _text(assignee.get("name"), 200)} if assignee and assignee.get("name") else None,
        "due": _day(task.get("due")),
        "url": url,
        "planId": plan_id if isinstance(plan_id, int) and not isinstance(plan_id, bool) else None,
        "itemKey": item_key if isinstance(item_key, str) and re.match(r"^[A-Za-z0-9_\-]{1,64}$", item_key) else None,
        "itemName": _text(task.get("itemName"), 500) or None,
        "linkedBy": _text(task.get("linkedBy"), 200) or None,
        "linkedAt": _text(task.get("linkedAt"), 40) or None,
        "updatedAt": _text(task.get("updatedAt"), 40) or None,
    }


def make_client(override=None):
    """Клиент по сохранённым настройкам; override — данные формы (проверка до сохранения). Без нового ключа в форме
    используется сохранённый — только для того же адреса."""
    settings = get_settings()
    if override is None:
        if not settings["baseUrl"] or not settings["keyEncrypted"]:
            raise PodeluError(NOT_CONFIGURED)
        client = PodeluLinksClient(settings["baseUrl"], decrypt_key(settings["keyEncrypted"]),
                                   settings["acceptInvalidCerts"], server_url=settings.get("serverUrl") or "")
        from app.services import system_health

        client.on_result = system_health.reporter("podelu_links")
        return client, settings["baseUrl"]
    base = check_base_url(override.get("baseUrl"))
    server = check_server_url(override.get("serverUrl"), base)
    key = (override.get("key") or "").strip()
    if not key:
        # сохранённый ключ уходит только на те адреса, для которых он был введён
        if not settings["keyEncrypted"] or settings["baseUrl"] != base or (settings.get("serverUrl") or "") != server:
            raise PodeluError("Введите ключ «По Делу»")
        if override.get("acceptInvalidCerts") and not settings["acceptInvalidCerts"]:
            raise PodeluError("Без проверки сертификата введите ключ заново")
        key = decrypt_key(settings["keyEncrypted"])
    return PodeluLinksClient(base, key, bool(override.get("acceptInvalidCerts")), server_url=server), base
