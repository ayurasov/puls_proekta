"""Связь с «По Делу» (todo-planner): вход сервисной учётной записью и выгрузки структуры и сотрудников (ADR-0009).

Подключение устроено так же, как к Jira (ADR-0007): одно на систему, его настраивает администратор
(«Администрирование → Интеграции»), пароль хранится зашифрованным (Fernet, ключ выводится из SECRET_KEY) и в
интерфейс не возвращается; сохранённый пароль уходит только на тот адрес, для которого он был введён. Запросы —
без перенаправлений, с таймаутами и повтором при 429/503; прокси — из переменных окружения сервера.

API-токенов в «По Делу» нет: вход — как у браузера. ``GET /api/auth/csrf-token`` → ``POST /api/auth/login``
с заголовком ``X-CSRF-Token`` → cookie сессии ``todo_planner_session`` → запросы → ``POST /api/auth/logout``.
В production «По Делу» проверяет у HTTPS-запросов с изменением данных заголовок Referer того же сайта
(WTF_CSRF_SSL_STRICT), поэтому клиент отправляет ``Referer: <адрес>/`` и ``Origin``.

Нужные права учётной записи в «По Делу»: выгрузка структуры (``GET /api/org/export``) — администратор или
полномочие «Ведёт структуру компании» (org.manage); выгрузка сотрудников (``GET /api/users/export``) —
администратор или «Ведёт учётные записи» (users.manage); список с идентификаторами (``GET /api/users/admin/all``)
— любое из двух.
"""

import base64
import hashlib
import io
import json
import os
import re
import time
import zipfile
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken

TIMEOUT = (5, 20)
EXPORT_TIMEOUT = (5, 120)      # выгрузка сотрудников с фото у большой компании собирается не мгновенно
MAX_ATTEMPTS = 3
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_PHOTO_BYTES = 5 * 1024 * 1024   # предел фото в «По Делу»; больше — архив повреждён или не тот
MAX_ARCHIVE_ENTRIES = 20000
SESSION_COOKIE = "todo_planner_session"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
STRUCTURE_RIGHT = "org.manage"
USERS_RIGHT = "users.manage"
RIGHT_LABELS = {STRUCTURE_RIGHT: "«Ведёт структуру компании»", USERS_RIGHT: "«Ведёт учётные записи»"}
NO_STRUCTURE_RIGHTS = ("У учётной записи нет прав на выгрузку структуры в «По Делу» — нужна роль «Администратор» "
                       "или полномочие «Ведёт структуру компании»")
NO_USERS_RIGHTS = ("У учётной записи нет прав на выгрузку сотрудников в «По Делу» — нужна роль «Администратор» "
                   "или полномочие «Ведёт учётные записи»")


class PodeluError(Exception):
    """Ошибка обращения к «По Делу» с понятным администратору текстом; status — код ответа, если он был."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def is_uuid(value):
    return isinstance(value, str) and bool(UUID_RE.match(value))


def check_base_url(base_url):
    """Адрес «По Делу»: http(s)://хост[:порт][/путь] — без логина, параметров и якоря. PODELU_ALLOWED_HOSTS
    (через запятую) ограничивает хосты: «podelu.company.ru» или «.company.ru» — домен и все поддомены."""
    url = (base_url or "").strip().rstrip("/")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise PodeluError("Адрес «По Делу» должен начинаться с http:// или https://")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — ValueError, если порт некорректный
    except ValueError:
        raise PodeluError("Некорректный адрес «По Делу»") from None
    if not parts.hostname or "@" in parts.netloc or parts.query or parts.fragment or "?" in url or "#" in url \
            or re.search(r"[\s\\%]", url):
        raise PodeluError("Адрес «По Делу» — только https://хост[:порт][/путь], без логина, параметров «?» и «#»")
    # Хост так, как его поймёт библиотека запросов: разное прочтение адреса обходило бы PODELU_ALLOWED_HOSTS
    try:
        from urllib3.util import parse_url

        if (parse_url(url).host or "").lower() != parts.hostname.lower():
            raise PodeluError("Некорректный адрес «По Делу»")
    except ValueError:
        raise PodeluError("Некорректный адрес «По Делу»") from None
    allowed = [h.strip().lower() for h in os.environ.get("PODELU_ALLOWED_HOSTS", "").split(",") if h.strip()]
    host = parts.hostname.lower()
    if allowed and not any(host == h or (h.startswith(".") and (host.endswith(h) or host == h[1:])) for h in allowed):
        raise PodeluError(f"Подключение к {host} запрещено настройками сервера (PODELU_ALLOWED_HOSTS)")
    return url


# ---------- хранение пароля ----------

def _fernet(secret_key):
    # Свой ключ (не как у токена Jira): зашифрованное значение одной интеграции не подходит другой
    key = base64.urlsafe_b64encode(hashlib.sha256(("podelu-password:" + str(secret_key)).encode()).digest())
    return Fernet(key)


def encrypt_password(secret_key, password):
    return _fernet(secret_key).encrypt(password.encode()).decode()


def decrypt_password(secret_key, encrypted):
    try:
        return _fernet(secret_key).decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError):
        raise PodeluError("Сохранённый пароль не удаётся расшифровать (сменился SECRET_KEY сервера) — "
                          "введите пароль заново") from None


def rights(user):
    """Права учётной записи «По Делу» для выгрузок: {structure, employees, admin}."""
    user = user if isinstance(user, dict) else {}
    admin = user.get("globalRole") == "admin"
    caps = user.get("capabilities") if isinstance(user.get("capabilities"), list) else []
    return {"admin": admin, "structure": admin or STRUCTURE_RIGHT in caps, "employees": admin or USERS_RIGHT in caps}


# ---------- HTTP-клиент ----------

class PodeluClient:
    """Сессия «По Делу»: login() → запросы → logout(). Как контекстный менеджер выходит из «По Делу» сам."""

    def __init__(self, base_url, login, password, accept_invalid_certs=False, timeout=TIMEOUT):
        self.base_url = check_base_url(base_url)
        if not login or not password:
            raise PodeluError("Укажите логин и пароль учётной записи «По Делу»")
        if re.search(r"[\x00-\x1f\x7f]", f"{login}{password}"):
            raise PodeluError("Логин или пароль содержит недопустимые символы — введите их заново")
        self._login = login
        self._password = password
        self.timeout = timeout
        self.csrf_token = None
        self.user = None
        parts = urlsplit(self.base_url)
        self.secure = parts.scheme.lower() == "https"
        self.session = requests.Session()  # прокси из окружения сервера подхватываются автоматически
        self.session.headers.update({
            "Accept": "application/json",
            # «По Делу» в production сверяет Referer HTTPS-запроса со своим адресом (WTF_CSRF_SSL_STRICT)
            "Referer": self.base_url + "/",
            "Origin": f"{parts.scheme}://{parts.netloc}",
            "User-Agent": "Puls-Proekta/podelu-sync",
        })
        self.session.verify = not accept_invalid_certs
        if accept_invalid_certs:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.logout()
        return False

    def url(self, path):
        return f"{self.base_url}/api/{path.lstrip('/')}"

    # ---------- запросы ----------

    def request(self, method, path, *, json_body=None, params=None, timeout=None, max_bytes=MAX_JSON_BYTES):
        """(ответ, тело) — с повтором при 429/503 и понятными сообщениями об ошибках. Тело читается не больше
        max_bytes: ответ больше — ошибка, а не память сервера."""
        url = self.url(path)
        headers = {"X-CSRF-Token": self.csrf_token} if method != "GET" and self.csrf_token else {}
        resp = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # без перенаправлений: пароль и cookie сессии уходят только на адрес из подключения
                resp = self.session.request(method, url, params=params, json=json_body, headers=headers,
                                            timeout=timeout or self.timeout, allow_redirects=False, stream=True)
            except requests.exceptions.SSLError:
                raise PodeluError("Ошибка TLS-сертификата «По Делу». Если используется самоподписанный или "
                                  "корпоративный сертификат, включите «Не проверять TLS-сертификат»") from None
            except requests.exceptions.ConnectTimeout:
                raise PodeluError(f"Превышено время ожидания подключения к {self.base_url}") from None
            except requests.exceptions.ReadTimeout:
                raise PodeluError("«По Делу» не ответила вовремя — попробуйте ещё раз") from None
            except requests.exceptions.ConnectionError:
                raise PodeluError(f"Не удалось подключиться к {self.base_url}: проверьте адрес и доступность "
                                  "сети") from None
            except (requests.RequestException, ValueError):
                # Прочие ошибки запроса — без текста исключения: в нём могли бы оказаться заголовки или тело
                raise PodeluError("Не удалось выполнить запрос к «По Делу» — проверьте адрес подключения") from None
            if resp.is_redirect:
                target = urlsplit(resp.headers.get("Location") or "")
                where = f" на {target.scheme}://{target.netloc}{target.path}" if target.netloc else ""
                resp.close()
                raise PodeluError(f"«По Делу» перенаправляет запрос ({resp.status_code}){where} — укажите в "
                                  "подключении итоговый адрес «По Делу» (например, с https://)", resp.status_code)
            retry = resp.status_code in (502, 503, 504) or (resp.status_code == 429 and method == "GET")
            if retry and attempt < MAX_ATTEMPTS:
                try:
                    delay = min(float(resp.headers.get("Retry-After", attempt)), 5.0)
                except ValueError:
                    delay = attempt
                resp.close()
                time.sleep(max(0.0, delay))
                continue
            break
        return resp, self._read(resp, max_bytes)

    @staticmethod
    def _read(resp, max_bytes):
        body = bytearray()
        try:
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > max_bytes:
                    raise PodeluError(f"Ответ «По Делу» больше {max_bytes // (1024 * 1024)} МБ — выгрузка не принята")
        except requests.RequestException:
            raise PodeluError("Ответ «По Делу» прерван — попробуйте ещё раз") from None
        finally:
            resp.close()
        return bytes(body)

    def check(self, resp, body, path, denied=None):
        """Ошибка с понятным текстом, если ответ не 200. denied — текст для 403 «нет прав»."""
        if resp.status_code == 200:
            return
        data = _json(body)
        code = data.get("error") if isinstance(data, dict) else None
        message = str(data.get("message") or "")[:300] if isinstance(data, dict) else ""
        status = resp.status_code
        if status == 401:
            if path == "auth/login":
                raise PodeluError("«По Делу» отклонила вход: неверный логин или пароль учётной записи подключения",
                                  401)
            raise PodeluError("«По Делу» не приняла сессию (401): проверьте, что cookie не блокируются прокси, "
                              "и повторите", 401)
        if status == 403:
            if code == "account_disabled":
                raise PodeluError("Учётная запись подключения отключена в «По Делу»", 403)
            if code == "no_account":
                raise PodeluError("Учётной записи подключения нет в «По Делу»" + (f": {message}" if message else ""),
                                  403)
            raise PodeluError(denied or ("Недостаточно прав в «По Делу» (403)" + (f": {message}" if message else "")),
                              403)
        if status == 400 and code == "csrf_error":
            raise PodeluError("«По Делу» отклонила запрос защитой от подделки (CSRF)"
                              + (f": {message}" if message else "")
                              + ". Укажите в подключении тот адрес, по которому открывается «По Делу» (та же схема "
                              "http/https и тот же хост)", 400)
        if status == 429:
            raise PodeluError("«По Делу» временно ограничила число запросов (429): было много попыток входа — "
                              "подождите несколько минут и повторите", 429)
        if status == 404:
            raise PodeluError(f"В «По Делу» нет {urlsplit(self.url(path)).path} (404) — проверьте адрес: нужен адрес, "
                              "по которому открывается «По Делу», и версия «По Делу» 1.4 или новее", 404)
        if status == 503 and code == "directory_unavailable":
            raise PodeluError("Сервер домена «По Делу» не отвечает — попробуйте позже", 503)
        if status >= 500:
            raise PodeluError(f"«По Делу» вернула ошибку {status} — попробуйте позже", status)
        raise PodeluError(f"«По Делу» вернула ошибку {status}" + (f": {message}" if message else ""), status)

    def get_json(self, path, kind=dict, denied=None, **kwargs):
        resp, body = self.request("GET", path, **kwargs)
        self.check(resp, body, path, denied)
        return self._expect(body, path, kind)

    def _expect(self, body, path, kind):
        data = _json(body)
        if not isinstance(data, kind):
            raise PodeluError(f"«По Делу» вернула неожиданный ответ ({urlsplit(self.url(path)).path}) — проверьте "
                              "адрес: возможно, отвечает не «По Делу», а страница входа или прокси")
        return data

    # ---------- вход и выход ----------

    def login(self):
        """Вход сервисной учётной записью; возвращает пользователя «По Делу» (имя, логин, роль, полномочия)."""
        data = self.get_json("auth/csrf-token")
        token = data.get("csrfToken")
        if not isinstance(token, str) or not token or len(token) > 500:
            raise PodeluError("«По Делу» не выдала CSRF-токен — проверьте адрес подключения")
        self.csrf_token = token
        resp, body = self.request("POST", "auth/login", json_body={"login": self._login, "password": self._password})
        self.check(resp, body, "auth/login")
        data = self._expect(body, "auth/login", dict)
        cookies = [cookie for cookie in self.session.cookies if cookie.name == SESSION_COOKIE] or list(self.session.cookies)
        if not cookies:
            raise PodeluError("«По Делу» не выдала cookie сессии после входа — проверьте адрес подключения")
        if not self.secure and any(cookie.secure for cookie in cookies):
            raise PodeluError("«По Делу» выдаёт cookie сессии только для HTTPS (SESSION_COOKIE_SECURE): укажите адрес "
                              "https://, по которому открывается «По Делу»")
        self.user = data.get("user") if isinstance(data.get("user"), dict) else {}
        return self.user

    def me(self):
        self.user = self.get_json("auth/me")
        return self.user

    def logout(self):
        """Выход из «По Делу» — сессия сервисной учётной записи не остаётся открытой. Ошибки не важны."""
        if self.csrf_token is None:
            self.session.close()
            return
        try:
            self.request("POST", "auth/logout", json_body={})
        except PodeluError:
            pass
        finally:
            self.csrf_token = None
            self.session.close()

    # ---------- выгрузки ----------

    def export_structure(self):
        """Файл структуры podelu.org-structure — как «Скачать файл структуры» в «По Делу»."""
        return self.get_json("org/export", denied=NO_STRUCTURE_RIGHTS, timeout=EXPORT_TIMEOUT)

    def export_employees(self, photos=True):
        """(документ podelu.employees, {путь фото в архиве: байты}). С фото — ZIP, без фото — JSON."""
        if not photos:
            return self.get_json("users/export", params={"format": "json", "inactive": "1"}, denied=NO_USERS_RIGHTS,
                                 timeout=EXPORT_TIMEOUT), {}
        params = {"format": "zip", "inactive": "1", "avatars": "1"}
        resp, body = self.request("GET", "users/export", params=params, timeout=EXPORT_TIMEOUT, max_bytes=MAX_ZIP_BYTES)
        self.check(resp, body, "users/export", NO_USERS_RIGHTS)
        return read_employees_zip(body)

    def people(self):
        """Все сотрудники «По Делу» с идентификаторами — для ссылок на карточки и сопоставления."""
        return self.get_json("users/admin/all", kind=list, denied=NO_USERS_RIGHTS)


def _json(body):
    try:
        return json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError):
        return None


def read_employees_zip(raw):
    """Архив выгрузки сотрудников → (документ из employees.json, {путь фото: байты}). Размеры проверяются до
    распаковки: архив не раздувается в памяти сервера."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        infos = {info.filename: info for info in archive.infolist()}
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise PodeluError("В архиве сотрудников «По Делу» слишком много файлов")
        info = infos.get("employees.json")
        if info is None:
            raise PodeluError("«По Делу» вернула архив без employees.json — проверьте версию «По Делу»")
        if info.file_size > MAX_JSON_BYTES:
            raise PodeluError("employees.json в архиве «По Делу» больше 20 МБ")
        document = _json(archive.read(info))
        if not isinstance(document, dict):
            raise PodeluError("employees.json в архиве «По Делу» повреждён")
        photos = {}
        for item in document.get("employees") or []:
            path = item.get("avatar") if isinstance(item, dict) else None
            entry = infos.get(path) if isinstance(path, str) and path.startswith("avatars/") else None
            if entry is not None and 0 < entry.file_size <= MAX_PHOTO_BYTES:
                photos[path] = archive.read(entry)
        return document, photos
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError, EOFError):
        raise PodeluError("«По Делу» вернула повреждённый архив сотрудников") from None
