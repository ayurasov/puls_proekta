"""Связь с Jira: подключение, чтение задач по ключам и JQL (ADR-0007).

Подключение устроено так же, как в burndown_tool и jira_mass_logger — одно на всю систему, его настраивает
администратор («Администрирование → Интеграции»):

* cloud        — Jira Cloud, email + API-токен (Basic), REST API v3;
* server       — Jira Server/DC, Personal Access Token (Bearer), REST API v2;
* server_basic — Jira Server/DC, логин + пароль (Basic), REST API v2.

Пароль или токен хранится зашифрованным (Fernet, ключ выводится из SECRET_KEY) и в интерфейс не возвращается;
сохранённый токен уходит только на тот адрес Jira, для которого он был введён. Запросы — без перенаправлений,
с повтором при 429/503; прокси берётся из переменных окружения сервера (HTTPS_PROXY и т. п.).
Сотрудникам без прав администратора ошибки показываются без адреса Jira и технических подробностей.
"""

import base64
import hashlib
import os
import re
import time
from datetime import date
from urllib.parse import parse_qs, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken

INSTANCE_TYPES = ("cloud", "server", "server_basic")
# Ключ задачи: проект до 50 символов и номер — так ключ гарантированно помещается в jira_issues.key
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}-\d{1,15}$")
_KEY_IN_TEXT = r"([A-Za-z][A-Za-z0-9_]{0,49}-\d{1,15})"
FIELD_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
BASE_FIELDS = ("summary", "status", "assignee", "issuetype", "priority", "duedate", "resolutiondate", "created",
               "updated", "timeoriginalestimate", "timespent", "timeestimate", "progress")
SEARCH_BATCH = 100
SEARCH_LIMIT = 1000
MAX_ATTEMPTS = 3
TIMEOUT = (5, 20)


class JiraError(Exception):
    """Ошибка обращения к Jira с понятным сотруднику текстом; status — код ответа Jira, если он был."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def check_base_url(base_url):
    """Адрес Jira: http(s)://хост[:порт][/путь] — без логина, параметров и якоря (иначе путь запросов к API
    можно подменить). JIRA_ALLOWED_HOSTS (через запятую) ограничивает хосты: «jira.company.ru» или «.company.ru» —
    домен и все поддомены."""
    url = (base_url or "").strip().rstrip("/")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise JiraError("Адрес Jira должен начинаться с http:// или https://")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — ValueError, если порт некорректный
    except ValueError:
        raise JiraError("Некорректный адрес Jira") from None
    if not parts.hostname or "@" in parts.netloc or parts.query or parts.fragment or "?" in url or "#" in url \
            or re.search(r"[\s\\%]", url):
        raise JiraError("Адрес Jira — только https://хост[:порт][/путь], без логина, параметров «?» и «#»")
    # Хост так, как его поймёт библиотека запросов: разное прочтение адреса обходило бы JIRA_ALLOWED_HOSTS
    try:
        from urllib3.util import parse_url

        if (parse_url(url).host or "").lower() != parts.hostname.lower():
            raise JiraError("Некорректный адрес Jira")
    except ValueError:
        raise JiraError("Некорректный адрес Jira") from None
    allowed = [h.strip().lower() for h in os.environ.get("JIRA_ALLOWED_HOSTS", "").split(",") if h.strip()]
    host = parts.hostname.lower()
    if allowed and not any(host == h or (h.startswith(".") and (host.endswith(h) or host == h[1:])) for h in allowed):
        raise JiraError(f"Подключение к {host} запрещено настройками сервера (JIRA_ALLOWED_HOSTS)")
    return url


# ---------- хранение токена ----------

def _fernet(secret_key):
    key = base64.urlsafe_b64encode(hashlib.sha256(("jira-token:" + str(secret_key)).encode()).digest())
    return Fernet(key)


def encrypt_token(secret_key, token):
    return _fernet(secret_key).encrypt(token.encode()).decode()


def decrypt_token(secret_key, encrypted):
    try:
        return _fernet(secret_key).decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError):
        raise JiraError("Сохранённый токен не удаётся расшифровать (сменился SECRET_KEY сервера) — "
                        "введите токен заново") from None


# ---------- ключи задач ----------

def parse_issue_keys(text):
    """«ABC-1, abc-2\\nXYZ-3» → (['ABC-1', 'ABC-2', 'XYZ-3'], некорректные) без повторов."""
    keys, invalid = [], []
    for raw in re.split(r"[\s,;]+", text or ""):
        if not raw:
            continue
        key = raw.upper()
        if ISSUE_KEY_RE.match(key):
            if key not in keys:
                keys.append(key)
        else:
            invalid.append(raw)
    return keys, invalid


def issue_key_from_url(url, base_url):
    """Ключ задачи из ссылки на подключённую Jira: …/browse/ABC-1, …?selectedIssue=ABC-1, …/issues/ABC-1.
    Ссылки на другие адреса не разбираются: задачу с тем же ключом в другой Jira искать нельзя."""
    if not url or not base_url:
        return None
    try:
        link, base = urlsplit(url.strip()), urlsplit(base_url)
        if (link.hostname or "").lower() != (base.hostname or "").lower() or link.port != base.port:
            return None
    except ValueError:  # например, порт вне диапазона в ссылке строки
        return None
    base_path = base.path.rstrip("/")
    if base_path and not (link.path == base_path or link.path.startswith(base_path + "/")):
        return None
    selected = parse_qs(link.query).get("selectedIssue")
    if selected and ISSUE_KEY_RE.match(selected[0].upper()):
        return selected[0].upper()
    match = re.search(r"/(?:browse|issues)/" + _KEY_IN_TEXT + r"/?$", link.path)
    return match.group(1).upper() if match else None


def browse_url(base_url, key):
    return f"{base_url.rstrip('/')}/browse/{key}"


# ---------- разбор задачи ----------

def _date(value):
    """'2026-11-20' или '2026-11-20T10:15:30.000+0300' → '2026-11-20'."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _hours(seconds):
    return round(seconds / 3600, 1) if isinstance(seconds, (int, float)) and seconds >= 0 else None


def _name(value):
    if isinstance(value, dict):
        return value.get("displayName") or value.get("name") or value.get("value") or ""
    return ""


def issue_progress(fields):
    """Прогресс задачи в процентах: затрачено / (затрачено + остаток), иначе поле progress Jira; None — нет данных."""
    spent, remaining = fields.get("timespent"), fields.get("timeestimate")
    if isinstance(spent, (int, float)) and isinstance(remaining, (int, float)) and spent + remaining > 0:
        return max(0, min(100, round(spent * 100 / (spent + remaining))))
    progress = fields.get("progress")
    if isinstance(progress, dict) and progress.get("total"):
        return max(0, min(100, round((progress.get("progress") or 0) * 100 / progress["total"])))
    return None


def normalize_issue(key, fields, base_url, field_map):
    """Поля задачи Jira → данные для интерфейса «Пульса»."""
    status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
    category = (status.get("statusCategory") or {}).get("key") or "new"
    if category not in ("new", "indeterminate", "done"):
        category = "new"
    start_field, end_field = field_map.get("startField") or "", field_map.get("endField") or "duedate"
    return {
        "key": key,
        "url": browse_url(base_url, key),
        "summary": str(fields.get("summary") or "")[:1000],
        "status": status.get("name") or "",
        "statusCategory": category,
        "type": _name(fields.get("issuetype")),
        "priority": _name(fields.get("priority")),
        "assignee": _name(fields.get("assignee")),
        "start": _date(fields.get(start_field)) if start_field else None,
        "due": _date(fields.get(end_field)),
        "resolved": _date(fields.get("resolutiondate")),
        "created": _date(fields.get("created")),
        "updated": fields.get("updated") if isinstance(fields.get("updated"), str) else None,
        "estimateHours": _hours(fields.get("timeoriginalestimate")),
        "spentHours": _hours(fields.get("timespent")),
        "remainingHours": _hours(fields.get("timeestimate")),
        "progress": issue_progress(fields),
    }


def request_fields(field_map):
    fields = list(BASE_FIELDS)
    for name in (field_map.get("startField"), field_map.get("endField")):
        if name and name not in fields:
            fields.append(name)
    return fields


# ---------- HTTP-клиент ----------

class JiraClient:
    def __init__(self, base_url, instance_type, login, token, accept_invalid_certs=False, detailed=True,
                 timeout=TIMEOUT):
        """detailed=False — для сотрудников без доступа к настройкам: в ошибках нет адреса Jira и подробностей."""
        if instance_type not in INSTANCE_TYPES:
            raise JiraError("Неизвестный тип подключения к Jira")
        self.base_url = check_base_url(base_url)
        if re.search(r"[\x00-\x1f\x7f]", f"{login}{token}"):
            raise JiraError("Логин или пароль (токен) содержит недопустимые символы — введите их заново")
        self.instance_type = instance_type
        self.detailed = detailed
        self.timeout = timeout
        self.session = requests.Session()  # прокси из окружения сервера подхватываются автоматически
        self.session.headers.update({"Accept": "application/json"})
        if instance_type == "server":
            self.session.headers["Authorization"] = "Bearer " + token
        else:
            # Basic: логин и пароль в UTF-8 (requests по умолчанию кодирует latin-1 и падает на кириллице)
            self.session.auth = (login.encode("utf-8"), token.encode("utf-8"))
        self.session.verify = not accept_invalid_certs
        if accept_invalid_certs:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @property
    def cloud(self):
        return self.instance_type == "cloud"

    def api(self, path):
        return f"{self.base_url}/rest/api/{'3' if self.cloud else '2'}/{path.lstrip('/')}"

    def fail(self, detailed, short, status=None):
        if self.detailed:
            raise JiraError(detailed, status)
        raise JiraError(short + " — сообщите администратору (подключение настраивается в разделе «Интеграции»)",
                        status)

    def where(self, url):
        return url if self.detailed else urlsplit(url).path

    def request(self, method, url, **kwargs):
        """Запрос с повтором при 429/503 (с учётом Retry-After) и понятными сообщениями об ошибках."""
        resp = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # без перенаправлений: запросы с токеном идут только на адрес из подключения
                resp = self.session.request(method, url, timeout=self.timeout, allow_redirects=False, **kwargs)
            except requests.exceptions.SSLError as exc:
                self.fail("Ошибка TLS/сертификата. Если Jira использует самоподписанный или корпоративный "
                          f"сертификат, включите «Не проверять TLS-сертификат». ({exc})", "Ошибка TLS-сертификата Jira")
            except requests.exceptions.ConnectTimeout:
                self.fail(f"Превышено время ожидания подключения к {self.base_url}", "Jira не отвечает")
            except requests.exceptions.ReadTimeout:
                raise JiraError("Jira не ответила вовремя — попробуйте ещё раз") from None
            except requests.exceptions.ConnectionError as exc:
                self.fail(f"Не удалось подключиться к {self.base_url}: проверьте адрес и доступность сети ({exc})",
                          "Не удалось подключиться к Jira")
            except (requests.RequestException, ValueError):
                # Прочие ошибки запроса (в том числе неверный заголовок) — без текста исключения: в нём могут
                # оказаться заголовки с токеном
                self.fail("Не удалось выполнить запрос к Jira — проверьте адрес и пароль или токен подключения",
                          "Не удалось выполнить запрос к Jira")
            if resp.is_redirect:
                target = urlsplit(resp.headers.get("Location") or "")
                where = f" на {target.scheme}://{target.netloc}{target.path}" if target.netloc else ""
                self.fail(f"Jira перенаправляет запрос ({resp.status_code}){where} — укажите в подключении итоговый "
                          "адрес Jira (например, с https:// или с путём /jira)",
                          "Jira перенаправляет запросы на другой адрес — подключение нужно обновить")
            if resp.status_code in (429, 503) and attempt < MAX_ATTEMPTS:
                try:
                    delay = min(float(resp.headers.get("Retry-After", attempt)), 5.0)
                except ValueError:
                    delay = attempt
                time.sleep(delay)
                continue
            return resp
        return resp

    def check(self, resp, url):
        if resp.status_code == 200:
            return
        denied = resp.headers.get("X-Authentication-Denied-Reason") or ""
        if resp.status_code in (401, 403) and "CAPTCHA" in denied.upper():
            self.fail("Jira требует ввести CAPTCHA для этой учётной записи (было несколько неудачных входов): "
                      "войдите в Jira через браузер под этой учётной записью, пройдите проверку и повторите",
                      "Jira требует ввести CAPTCHA для учётной записи подключения", resp.status_code)
        if resp.status_code == 401:
            self.fail("Jira отклонила логин/токен (401). Проверьте тип подключения, логин и пароль или токен; "
                      "если в Jira отключён вход по паролю (Basic), используйте Personal Access Token",
                      "Jira отклонила логин или пароль подключения (401)", 401)
        detail = _error_text(resp)
        if resp.status_code == 403:
            raise JiraError("Недостаточно прав в Jira (403)" + (f": {detail}" if detail else ""), 403)
        raise JiraError(f"Jira вернула ошибку {resp.status_code}: {detail or self.where(url)}", resp.status_code)

    def json(self, method, url, kind=dict, **kwargs):
        resp = self.request(method, url, **kwargs)
        self.check(resp, url)
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, kind):
            self.fail(f"Jira вернула неожиданный ответ ({url}) — проверьте адрес Jira; возможно, отвечает страница "
                      "входа или прокси",
                      f"Jira вернула неожиданный ответ ({self.where(url)}): возможно, отвечает страница входа или прокси")
        return data

    # ---------- операции ----------

    def myself(self):
        data = self.json("GET", self.api("myself"))
        return data.get("displayName") or data.get("name") or data.get("emailAddress") or ""

    def search(self, jql, fields, limit=SEARCH_LIMIT):
        """Задачи по JQL постранично (startAt в Server/DC, nextPageToken в Cloud) → [(ключ, поля)]."""
        out, start_at, token = [], 0, None
        while True:
            body = {"jql": jql, "fields": list(fields), "maxResults": min(SEARCH_BATCH, limit - len(out))}
            if self.cloud:
                if token:
                    body["nextPageToken"] = token
            else:
                body["startAt"] = start_at
                body["validateQuery"] = "warn"  # несуществующий ключ в «key in (…)» — предупреждение, а не ошибка
            data = self.json("POST", self.api("search/jql" if self.cloud else "search"), json=body)
            issues = [i for i in data.get("issues") or [] if isinstance(i, dict) and i.get("key")]
            out.extend((i["key"], i.get("fields") or {}) for i in issues)
            start_at += len(issues)
            token = data.get("nextPageToken")
            more = bool(token) if self.cloud else start_at < (data.get("total") or 0)
            if not issues or not more or len(out) >= limit:
                return out

    def issue(self, key, fields):
        """Поля задачи по ключу; None — задачи нет или нет прав на просмотр."""
        url = self.api(f"issue/{key}")
        resp = self.request("GET", url, params={"fields": ",".join(fields)})
        if resp.status_code == 404:
            return None
        self.check(resp, url)
        try:
            data = resp.json()
        except ValueError:
            data = None
        return (data.get("fields") or {}) if isinstance(data, dict) else None

    def issues_by_keys(self, keys, fields, deadline=None):
        """({ключ: поля}, проверенные ключи) для задач по ключам пакетами; удалённые и недоступные задачи в ответ не
        попадают. Если Jira отклоняет пакет из-за несуществующего ключа (Cloud), пакет делится пополам, пока не
        останется один ключ, — несколько ошибочных ключей стоят десятков запросов, а не сотен. deadline
        (time.monotonic()) — к этому времени чтение прекращается: остальное прочитается при следующем открытии."""
        out, checked = {}, set()
        pending = [sorted(set(keys))[i:i + SEARCH_BATCH] for i in range(0, len(set(keys)), SEARCH_BATCH)]
        while pending:
            if deadline is not None and time.monotonic() > deadline:
                return out, checked
            batch = pending.pop(0)
            try:
                found = self.search("key in (%s)" % ", ".join(batch), fields, limit=len(batch))
            except JiraError as exc:
                if exc.status != 400:
                    raise
                if len(batch) > 1:
                    pending[:0] = [batch[:len(batch) // 2], batch[len(batch) // 2:]]
                    continue
                found = []
            returned = {key.upper(): data for key, data in found}
            out.update({key: data for key, data in returned.items() if key in batch})
            # Задача перенесена в другой проект: Jira вернула её под новым ключом — читаем по старому
            if set(returned) - set(batch):
                for key in [k for k in batch if k not in out][:20]:
                    data = self.issue(key, fields)
                    if data is not None:
                        out[key] = data
            checked.update(batch)
        return out, checked

    def date_fields(self):
        """Поля Jira с датой — для выбора полей «начало» и «срок» задачи."""
        data = self.json("GET", self.api("field"), kind=list)
        out = []
        for f in data:
            if isinstance(f, dict) and (f.get("schema") or {}).get("type") in ("date", "datetime"):
                fid = f.get("id") or ""
                if FIELD_ID_RE.match(fid):
                    out.append({"id": fid, "name": f.get("name") or fid, "custom": bool(f.get("custom"))})
        out.sort(key=lambda f: (f["custom"], f["name"].lower()))
        return out


def _error_text(resp):
    """Сообщения из ответа Jira (errorMessages, errors, message). Произвольное тело не показывается: адрес мог
    указывать не на Jira."""
    try:
        data = resp.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    messages = [str(m) for m in data.get("errorMessages") or [] if isinstance(data.get("errorMessages"), list)]
    errors = data.get("errors")
    if isinstance(errors, dict):
        messages += [f"{k}: {v}" for k, v in errors.items()]
    if not messages and isinstance(data.get("message"), str):
        messages.append(data["message"])
    return "; ".join(messages)[:300]

