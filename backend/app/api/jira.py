"""Jira: подключение (администратор), диагностика и задачи, привязанные к строкам планов (ADR-0007).

Сотрудникам, которые правят план: проекты Jira, их типы задач, статусы, релизы, спринты и эпики, поиск задач по
фильтрам (без знания JQL), подсказки при привязке строки, новые задачи источника плана.
"""

import re
import time
from typing import Literal, Optional

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import BaseModel, Field

from app import audit
from app.api.plans import _plan_with_access
from app.extensions import db, limiter
from app.jira_client import (
    EPIC_TYPE_NAMES, FIELD_ID_RE, ISSUE_KEY_RE, PROJECT_KEY_RE, JiraError, browse_url, build_jql, check_base_url,
    check_link_url, encrypt_token, is_epic, issue_key_from_url, jql_string, normalize_issue, parse_issue_keys,
    request_fields,
)
from app.security import ApiError, admin_required, current_user, login_required
from app.services import jira as jira_service

bp = Blueprint("jira", __name__, url_prefix="/api")

SEARCH_MAX = 200


def _user_key():
    return f"user:{session.get('user_id')}"


def _jira_failed(exc):
    return ApiError(502, "jira_error", str(exc))


class ConnectionIn(BaseModel):
    baseUrl: str = Field(max_length=500)
    instanceType: Literal["cloud", "server", "server_basic"] = "server_basic"
    login: str = Field(default="", max_length=300)
    token: str = Field(default="", max_length=2000)
    acceptInvalidCerts: bool = False
    startField: str = Field(default="", max_length=100)
    endField: str = Field(default="duedate", max_length=100)
    linkUrls: list[str] = Field(default_factory=list, max_length=jira_service.MAX_LINK_URLS)


def _form(payload):
    data = payload.model_dump()
    data["login"] = data["login"].strip()
    # Токен вставляют из буфера — с пробелами и переводом строки по краям; управляющие символы внутри — ошибка
    data["token"] = data["token"].strip()
    if re.search(r"[\x00-\x1f\x7f]", data["token"] + data["login"]):
        raise ApiError(400, "validation_error", "Логин или пароль (токен) содержит недопустимые символы")
    try:
        data["baseUrl"] = check_base_url(data["baseUrl"])
    except JiraError as exc:
        raise ApiError(400, "validation_error", str(exc)) from None
    for name in ("startField", "endField"):
        data[name] = data[name].strip()
        if data[name] and not FIELD_ID_RE.match(data[name]):
            raise ApiError(400, "validation_error", "Некорректный идентификатор поля Jira")
    data["endField"] = data["endField"] or "duedate"
    links = []
    for url in data["linkUrls"]:
        if not (url or "").strip():
            continue
        try:
            url = check_link_url(url[:500])
        except JiraError as exc:
            raise ApiError(400, "validation_error", str(exc)) from None
        if url != data["baseUrl"] and url not in links:
            links.append(url)
    data["linkUrls"] = links
    if data["instanceType"] != "server" and not data["login"]:
        raise ApiError(400, "validation_error", "Укажите email или логин")
    return data


# ---------- администратор: «Интеграции» ----------

@bp.get("/settings/jira")
@admin_required
def get_connection():
    return jsonify(jira_service.admin_json(jira_service.get_connection()))


@bp.put("/settings/jira")
@admin_required
def put_connection():
    data = _form(ConnectionIn.model_validate(request.get_json(silent=True) or {}))
    existing = jira_service.get_connection()
    token = data.pop("token")
    if not token and not (existing and existing["tokenEncrypted"]):
        raise ApiError(400, "validation_error", "Укажите пароль или токен")
    if not token and existing["baseUrl"] != data["baseUrl"]:
        # сохранённый пароль уходит только на тот адрес Jira, для которого он был введён
        raise ApiError(400, "validation_error", "Адрес Jira изменён — введите пароль или токен заново")
    if not token and data["acceptInvalidCerts"] and not existing["acceptInvalidCerts"]:
        # без проверки сертификата сохранённый пароль мог бы уйти подменённому серверу — подтвердите его заново
        raise ApiError(400, "validation_error", "Без проверки сертификата введите пароль или токен заново")
    data["tokenEncrypted"] = encrypt_token(current_app.secret_key, token) if token else existing["tokenEncrypted"]
    jira_service.save_connection(data)
    old = existing or {}
    changed = [k for k in ("baseUrl", "instanceType", "login", "acceptInvalidCerts", "startField", "endField",
                           "linkUrls") if (old.get(k) or ([] if k == "linkUrls" else old.get(k))) != data.get(k)]
    if token:
        changed.append("token")
    if existing is None or any(old.get(k) != data.get(k) for k in ("baseUrl", "instanceType", "login", "startField",
                                                                    "endField")):
        jira_service.clear_cache()
    jira_service.reset_backoff()
    if changed:
        audit.record("Настройки", "update" if existing else "create", "Jira", ", ".join(changed)[:100],
                     old.get("baseUrl") or "", data["baseUrl"])
    db.session.commit()
    return jsonify(jira_service.admin_json(jira_service.get_connection()))


@bp.delete("/settings/jira")
@admin_required
def delete_connection():
    existing = jira_service.get_connection()
    if existing:
        jira_service.delete_connection()
        audit.record("Настройки", "delete", "Jira", "", existing["baseUrl"], "")
        db.session.commit()
    return jsonify({"ok": True})


def _admin_client():
    payload = request.get_json(silent=True)
    override = _form(ConnectionIn.model_validate(payload)) if payload else None
    try:
        return jira_service.make_client(override=override, detailed=True)
    except JiraError as exc:
        raise _jira_failed(exc) from None


# Проверочный поиск: ограниченный запрос (Cloud не принимает JQL без условия), без проверки JQL — так же, как
# читаются задачи строк плана
PROBE_JQL = "updated >= -1d ORDER BY updated DESC"


@bp.post("/settings/jira/test")
@admin_required
def test_connection():
    """Вход, версия Jira и поиск задач — тем же запросом, каким читаются задачи планов: «соединение установлено»
    значит, что задачи действительно будут загружаться."""
    client = _admin_client()
    try:
        user = client.myself()
    except JiraError as exc:
        raise _jira_failed(exc) from None
    try:
        info = client.server_info()
    except JiraError:
        info = {"version": "", "deploymentType": ""}
    try:
        client.search(PROBE_JQL, ["summary"], limit=1, strict=False)
    except JiraError as exc:
        raise ApiError(502, "jira_error", f"Вход выполнен ({user}), но поиск задач в Jira не работает: {exc}") from None
    warnings = []
    try:
        projects = len(client.projects())
    except JiraError:
        projects = None
    if projects == 0:
        warnings.append("Учётная запись не видит ни одного проекта Jira — задачи не будут загружаться. Выдайте ей право "
                        "«Просмотр проектов» (Browse Projects) в нужных проектах.")
    if info.get("baseUrl") and not _same_jira(info["baseUrl"], client.base_url):
        warnings.append(f"Jira сообщает свой адрес как {info['baseUrl']} — ссылки на задачи, скопированные из Jira, "
                        "ведут туда. Добавьте его в «Другие адреса этой Jira», иначе такие строки не будут связаны с "
                        "задачами.")
    return jsonify({"ok": True, "user": user, **info, "projects": projects, "warnings": warnings})


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _ru_date(value):
    return f"{value[8:10]}.{value[5:7]}.{value[:4]}" if value and len(value) >= 10 else value


def _same_jira(url, base):
    """Тот же ли это адрес Jira для ссылок (хост, порт и путь; http и https не различаются)."""
    return issue_key_from_url(browse_url(url, "ABC-1"), base) == "ABC-1"


DIAG_OK, DIAG_WARN, DIAG_ERROR, DIAG_SKIP = "ok", "warn", "error", "skip"


@bp.post("/settings/jira/diagnose")
@admin_required
@limiter.limit("10 per minute", key_func=_user_key)
def diagnose():
    """Пошаговая проверка: адрес и сеть, вход, права, проекты, поиск, поля, задача по ключу, ссылки строк планов.
    Ответ — [{id, title, status: ok|warn|error|skip, detail}] — объясняет администратору, почему задачи не
    загружаются, даже когда «соединение установлено»."""
    payload = request.get_json(silent=True) or {}
    issue_key = str(payload.pop("issueKey", "") or "").strip().upper()[:70]
    override = _form(ConnectionIn.model_validate(payload)) if payload.get("baseUrl") else None
    steps = []

    def step(sid, title, status, detail=""):
        steps.append({"id": sid, "title": title, "status": status, "detail": detail})

    try:
        client = jira_service.make_client(override=override, detailed=True)
    except JiraError as exc:
        step("config", "Подключение", DIAG_ERROR, str(exc))
        return jsonify({"steps": steps})
    conn = override or jira_service.get_connection() or {}
    db.session.rollback()  # к Jira — без открытой транзакции базы
    started = time.monotonic()
    try:
        info = client.server_info()
        kind = {"Cloud": "Jira Cloud", "Server": "Jira Server", "DataCenter": "Jira Data Center"}.get(
            info["deploymentType"], "Jira")
        step("network", "Адрес и сеть", DIAG_OK, f"{kind} {info['version']} отвечает по адресу {client.base_url} "
             f"({round((time.monotonic() - started) * 1000)} мс)")
        if (client.cloud and info["deploymentType"] not in ("", "Cloud")) or \
                (not client.cloud and info["deploymentType"] == "Cloud"):
            step("type", "Способ входа", DIAG_WARN, "Тип Jira не совпадает со способом входа: для Jira Cloud выберите «email и "
                 "API-токен», для Server/Data Center — PAT или логин и пароль")
        if info.get("baseUrl") and not _same_jira(info["baseUrl"], client.base_url):
            known = [u for u in conn.get("linkUrls") or [] if _same_jira(info["baseUrl"], u)]
            step("baseurl", "Адрес ссылок на задачи", DIAG_OK if known else DIAG_WARN,
                 f"Jira считает своим адресом {info['baseUrl']}"
                 + (" — он есть в «Других адресах этой Jira»" if known else
                    " — добавьте его в «Другие адреса этой Jira», иначе ссылки, скопированные из Jira, не распознаются"))
    except JiraError as exc:
        step("network", "Адрес и сеть", DIAG_ERROR, str(exc))
        return jsonify({"steps": steps})
    try:
        user = client.myself()
        step("login", "Вход", DIAG_OK, f"Учётная запись: {user}")
    except JiraError as exc:
        step("login", "Вход", DIAG_ERROR, str(exc))
        return jsonify({"steps": steps})
    try:
        permission = client.browse_permission()
        if permission is False:
            step("permission", "Право «Просмотр проектов»", DIAG_ERROR,
                 "У учётной записи нет права «Просмотр проектов» (Browse Projects) ни в одном проекте — Jira не отдаст "
                 "ей ни одной задачи. Добавьте учётную запись в роль проекта или группу с этим правом.")
        elif permission:
            step("permission", "Право «Просмотр проектов»", DIAG_OK, "Есть")
    except JiraError as exc:
        step("permission", "Право «Просмотр проектов»", DIAG_WARN, f"Не удалось проверить: {exc}")
    try:
        projects = client.projects()
        names = ", ".join(f"{p['key']} — {p['name']}" for p in projects[:5])
        more = f" и ещё {len(projects) - 5}" if len(projects) > 5 else ""
        step("projects", "Видимые проекты", DIAG_OK if projects else DIAG_ERROR,
             f"{len(projects)}: {names}{more}" if projects else
             "Учётная запись не видит ни одного проекта — задачи не будут загружаться")
    except JiraError as exc:
        step("projects", "Видимые проекты", DIAG_WARN, f"Список проектов не получен: {exc}")
    try:
        found, total = client.search(PROBE_JQL, ["summary"], limit=1, strict=False, with_total=True)
        how = "GET" if client._search_get.get((client.base_url, client.instance_type)) else "POST"
        detail = f"Поиск работает ({how} {'/search/jql' if client.cloud else '/search'})"
        if total is not None:
            detail += f"; задач, изменённых за сутки: {total}"
        step("search", "Поиск задач", DIAG_OK, detail)
    except JiraError as exc:
        step("search", "Поиск задач", DIAG_ERROR, str(exc))
    try:
        all_fields = client.fields()
        ids = {f.get("id"): f.get("name") for f in all_fields}
        fmap = jira_service.field_map(conn)
        problems = [f"«{name}» ({fid}) не найдено" for name, fid in (("Начало", fmap["startField"]),
                                                                      ("Срок", fmap["endField"])) if fid and fid not in ids]
        special = client.special_fields(all_fields)
        extras = []
        if not client.cloud:
            extras.append(f"«Epic Link» — {special['epicField']}" if special["epicField"] else "поля «Epic Link» нет")
        extras.append(f"«Sprint» — {special['sprintField']}" if special["sprintField"] else "поля «Sprint» нет (без Jira "
                      "Software спринты недоступны)")
        start = f"начало — {ids.get(fmap['startField'], fmap['startField'])}" if fmap["startField"] else \
            "поле начала не выбрано (начало задачи из Jira не переносится)"
        step("fields", "Поля дат, эпиков и спринтов", DIAG_ERROR if problems else DIAG_OK,
             "; ".join(problems) if problems else
             f"Срок — {ids.get(fmap['endField'], fmap['endField'])}; {start}; {'; '.join(extras)}")
    except JiraError as exc:
        step("fields", "Поля дат, эпиков и спринтов", DIAG_WARN, f"Список полей не получен: {exc}")
    if issue_key:
        if not ISSUE_KEY_RE.match(issue_key):
            step("issue", f"Задача {issue_key}", DIAG_ERROR, "Не похоже на ключ задачи (ABC-123)")
        else:
            try:
                fmap = jira_service.field_map(conn)
                data = client.issue(issue_key, request_fields(fmap))
                if data is None:
                    step("issue", f"Задача {issue_key}", DIAG_ERROR,
                         "Jira ответила «не найдена»: задачи нет или у учётной записи нет права её видеть")
                else:
                    issue = normalize_issue(issue_key, data, client.base_url, fmap)
                    step("issue", f"Задача {issue_key}", DIAG_OK,
                         f"«{issue['summary']}» · {issue['status'] or 'без статуса'} · "
                         f"{issue['assignee'] or 'без исполнителя'} · срок {_ru_date(issue['due']) or 'не задан'}")
            except JiraError as exc:
                step("issue", f"Задача {issue_key}", DIAG_ERROR, str(exc))
    census = jira_service.link_census({**conn, "baseUrl": client.base_url})
    if census["recognized"] or census["foreignRows"]:
        foreign = ", ".join(f"{f['host']} ({f['rows']})" for f in census["foreign"])
        detail = (f"Строк, связанных с задачами этой Jira: {census['recognized']} ({census['issues']} "
                  f"{_plural(census['issues'], 'задача', 'задачи', 'задач')})")
        if census["foreignRows"]:
            rows_word = _plural(census["foreignRows"], "строка ссылается", "строки ссылаются", "строк ссылаются")
            detail += (f". Ещё {census['foreignRows']} {rows_word} на задачи по другим адресам: {foreign} — если это та "
                       "же Jira, добавьте адрес в «Другие адреса этой Jira»")
        step("links", "Ссылки строк планов", DIAG_WARN if census["foreignRows"] else DIAG_OK, detail)
    else:
        step("links", "Ссылки строк планов", DIAG_SKIP, "В планах пока нет строк со ссылками на задачи Jira")
    return jsonify({"steps": steps})


@bp.post("/settings/jira/fields")
@admin_required
def date_fields():
    """Поля Jira с датой — для выбора «начала» и «срока» задачи (по форме или сохранённому подключению)."""
    client = _admin_client()
    try:
        return jsonify(client.date_fields())
    except JiraError as exc:
        raise _jira_failed(exc) from None


# ---------- сотрудники ----------

@bp.get("/jira")
@login_required
def status():
    return jsonify(jira_service.public_json(jira_service.get_connection()))


@bp.get("/plans/<int:plan_id>/jira")
@login_required
@limiter.limit("20 per minute", key_func=_user_key, exempt_when=lambda: request.args.get("refresh") != "1")
def plan_issues(plan_id):
    """Задачи Jira строк плана: {issues: {ключ: данные | null}, rows: {ключ: [строки]}, error, fetchedAt}."""
    plan, _project, _level = _plan_with_access(plan_id)
    conn = jira_service.get_connection()
    if not conn:
        return jsonify({"connected": False, "issues": {}, "rows": {}, "error": None, "fetchedAt": None})
    foreign = {}
    rows = jira_service.plan_issue_keys(plan.id, conn, foreign)
    issues, error, fetched_at = {}, None, None
    if rows:
        try:
            issues, error, fetched_at = jira_service.fetch_issues(
                list(rows), refresh=request.args.get("refresh") == "1", detailed=current_user().role == "admin")
        except JiraError as exc:
            error = str(exc)
    return jsonify({"connected": True, "issues": issues, "rows": rows, "error": error,
                    "fetchedAt": fetched_at.isoformat() + "Z" if fetched_at else None,
                    # Строки со ссылками на задачи по другому адресу: подсказка администратору («Другие адреса»)
                    "foreign": [{"host": h, "rows": n} for h, n in sorted(foreign.items(), key=lambda kv: -kv[1])[:5]]})


class SearchIn(BaseModel):
    planId: int
    keys: str = Field(default="", max_length=20000)
    jql: str = Field(default="", max_length=2000)
    limit: Optional[int] = Field(default=SEARCH_MAX, ge=1, le=SEARCH_MAX)
    refresh: bool = False
    # Разложить по эпикам: у задач — ключ эпика, в ответе — эпики (название, ссылка, статус)
    epics: bool = False
    # Поиск без JQL: проект, текст, типы, категории статусов, релиз, спринт, эпик (jira_client.build_jql)
    filter: Optional[dict] = None
    # Расширенные данные задач: проект, спринты, релизы, связи «блокирует» — для групп по спринтам, вех и связей
    extended: bool = False


@bp.post("/jira/search")
@login_required
@limiter.limit("30 per minute", key_func=_user_key)
def search():
    """Задачи для «Добавить из Jira» и только что привязанных строк: по ключам или по JQL (не больше 200).
    Только сотруднику, который может править план: задачи читаются учётной записью подключения, и видеть их
    вне плана нельзя (ADR-0007)."""
    user = current_user()
    payload = SearchIn.model_validate(request.get_json(silent=True) or {})
    _plan_with_access(payload.planId, "edit")
    conn = jira_service.get_connection()
    if not conn:
        raise ApiError(409, "jira_not_configured", jira_service.NOT_CONFIGURED)
    keys, invalid = parse_issue_keys(payload.keys)
    detailed = user.role == "admin"
    if payload.filter is not None and not keys and not payload.jql.strip():
        try:
            client = jira_service.make_client(detailed=detailed)
            special = jira_service.special_fields(client) if payload.filter.get("epic") and not client.cloud else {}
            payload.jql = build_jql(payload.filter, cloud=client.cloud, epic_field=special.get("epicField"))
        except JiraError as exc:
            if exc.status is None:
                raise ApiError(400, "validation_error", str(exc)) from None
            raise _jira_failed(exc) from None
    if not keys and not payload.jql.strip():
        raise ApiError(400, "validation_error", "Укажите ключи задач или запрос JQL")
    if payload.epics or payload.extended:
        return jsonify(_search_with_epics(conn, keys, invalid, payload, detailed))
    if keys:
        # По ключам — через кэш: так же карточка строки получает задачу сразу после ввода ссылки
        wanted = keys[:SEARCH_MAX]
        found, error, _fetched = jira_service.fetch_issues(wanted, refresh=payload.refresh, detailed=detailed)
        if error and not any(k in found for k in wanted):
            raise ApiError(502, "jira_error", error)
        return jsonify({"issues": [found[k] for k in wanted if found.get(k)],
                        "missing": [k for k in wanted if k in found and found[k] is None],
                        "invalid": invalid, "truncated": len(keys) > SEARCH_MAX, "error": error})
    fmap = jira_service.field_map(conn)
    try:
        client = jira_service.make_client(detailed=detailed)
        ordered = client.search(payload.jql.strip(), request_fields(fmap), limit=payload.limit or SEARCH_MAX)
    except JiraError as exc:
        if exc.status == 400 and exc.detail:
            raise ApiError(400, "jql_error", f"Jira не приняла запрос: {exc.detail}") from None
        raise _jira_failed(exc) from None
    return jsonify({"issues": [normalize_issue(k.upper(), f, conn["baseUrl"], fmap) for k, f in ordered],
                    "missing": [], "invalid": invalid, "truncated": len(ordered) >= (payload.limit or SEARCH_MAX),
                    "error": None})


def _search_with_epics(conn, keys, invalid, payload, detailed):
    """Поиск для «Задач из Jira» с эпиками, спринтами, релизами и связями: поле «Epic Link» (Server/DC) или родитель
    (Cloud), эпики — отдельным запросом. Кэш задач строк не используется: в нём нет этих полей."""
    try:
        client = jira_service.make_client(detailed=detailed)
        special = jira_service.special_fields(client)
        fmap = {**jira_service.field_map(conn), "epicField": special["epicField"], "sprintField": special["sprintField"]}
        fields = request_fields(fmap, epics=True, extended=payload.extended)
        if keys:
            wanted = keys[:SEARCH_MAX]
            found, _checked = client.issues_by_keys(wanted, fields)
            ordered = [(k, found[k]) for k in wanted if k in found]
            missing = [k for k in wanted if k not in found]
        else:
            ordered = [(k.upper(), f) for k, f in client.search(payload.jql.strip(), fields,
                                                                 limit=payload.limit or SEARCH_MAX)]
            missing = []
        issues = [normalize_issue(k, f, conn["baseUrl"], fmap) for k, f in ordered]
        epic_keys = sorted({i["epic"] for i in issues if i["epic"]} - {i["key"] for i in issues})[:SEARCH_MAX]
        epics = {i["key"]: i for i in issues if i["isEpic"]}
        if epic_keys:
            found, _checked = client.issues_by_keys(epic_keys, request_fields(fmap))
            epics.update({k: normalize_issue(k, f, conn["baseUrl"], fmap) for k, f in found.items()})
    except JiraError as exc:
        if exc.status == 400 and exc.detail:
            raise ApiError(400, "jql_error", f"Jira не приняла запрос: {exc.detail}") from None
        raise _jira_failed(exc) from None
    return {"issues": issues, "missing": missing, "invalid": invalid,
            "truncated": len(keys) > SEARCH_MAX if keys else len(ordered) >= (payload.limit or SEARCH_MAX),
            "error": None, "jql": payload.jql.strip() if not keys else None,
            "epics": {k: {"key": k, "summary": e["summary"], "url": e["url"], "status": e["status"],
                          "statusCategory": e["statusCategory"]} for k, e in epics.items()}}


# ---------- проекты Jira, подсказки, источник плана ----------

def _editor_client(plan_id):
    """Клиент Jira для сотрудника, который правит план: задачи читаются учётной записью подключения, и видеть
    их вне плана нельзя (ADR-0007)."""
    _plan_with_access(plan_id, "edit")
    conn = jira_service.get_connection()
    if not conn:
        raise ApiError(409, "jira_not_configured", jira_service.NOT_CONFIGURED)
    try:
        return conn, jira_service.make_client(detailed=current_user().role == "admin")
    except JiraError as exc:
        raise _jira_failed(exc) from None


def _plan_id_arg():
    try:
        return int(request.args.get("planId", ""))
    except ValueError:
        raise ApiError(400, "validation_error", "Укажите план") from None


@bp.get("/jira/projects")
@login_required
@limiter.limit("30 per minute", key_func=_user_key)
def jira_projects():
    """Проекты Jira, которые видит учётная запись подключения (для выбора проекта вместо JQL)."""
    _conn, client = _editor_client(_plan_id_arg())
    try:
        return jsonify({"projects": jira_service.projects(client, refresh=request.args.get("refresh") == "1")})
    except JiraError as exc:
        raise _jira_failed(exc) from None


@bp.get("/jira/projects/<key>/meta")
@login_required
@limiter.limit("30 per minute", key_func=_user_key)
def jira_project_meta(key):
    """Типы задач, статусы, релизы, спринты и эпики проекта Jira — для фильтров «Задач из Jira» и вех релизов.
    Что не удалось получить (например, спринты без Jira Software), приходит пустым списком."""
    key = key.upper()
    if not PROJECT_KEY_RE.match(key):
        raise ApiError(400, "validation_error", "Некорректный ключ проекта Jira")
    conn, client = _editor_client(_plan_id_arg())
    out = {"project": key, "types": [], "statuses": [], "versions": [], "sprints": [], "epics": [], "warnings": []}
    try:
        out["types"], out["statuses"] = client.issue_types(key)
    except JiraError as exc:
        if exc.status == 404:
            raise ApiError(404, "not_found", f"Проект {key} не найден в Jira или недоступен учётной записи") from None
        if exc.status not in (400, 403):
            raise _jira_failed(exc) from None
        out["warnings"].append("Типы задач и статусы не получены")
    try:
        out["versions"] = [v for v in client.versions(key) if not v["archived"]]
    except JiraError as exc:
        if exc.status not in (400, 403, 404):
            raise _jira_failed(exc) from None
        out["warnings"].append("Релизы не получены")
    try:
        out["sprints"] = client.sprints(key)
    except JiraError as exc:
        if exc.status not in (400, 403, 404):
            raise _jira_failed(exc) from None
    # Тип «Эпик» — по названию из типов проекта (в русской Jira он может называться «Эпик»); нет типа — нет эпиков
    epic_type = next((t for t in out["types"] if t.lower() in EPIC_TYPE_NAMES), None)
    try:
        epics = client.search(f"project = {key} AND issuetype = {jql_string(epic_type)} AND statusCategory != 3 "
                              "ORDER BY Rank ASC", ["summary", "status", "issuetype"], limit=100) if epic_type else []
        fmap = jira_service.field_map(conn)
        out["epics"] = [{"key": k.upper(), "summary": normalize_issue(k.upper(), f, conn["baseUrl"], fmap)["summary"]}
                        for k, f in epics]
    except JiraError as exc:
        if exc.status not in (400, 403, 404):
            raise _jira_failed(exc) from None
    return jsonify(out)


class SuggestIn(BaseModel):
    planId: int
    text: str = Field(min_length=1, max_length=200)
    project: str = Field(default="", max_length=50)


@bp.post("/jira/suggest")
@login_required
@limiter.limit("60 per minute", key_func=_user_key)
def suggest():
    """Подсказки при привязке строки: задачи по ключу или словам из названия (не больше 10), сначала — в
    проекте-источнике плана."""
    payload = SuggestIn.model_validate(request.get_json(silent=True) or {})
    conn, client = _editor_client(payload.planId)
    text = payload.text.strip()
    project = payload.project.strip().upper()
    filters = {"text": text, "order": "updated"}
    if project and PROJECT_KEY_RE.match(project) and not ISSUE_KEY_RE.match(text.upper()):
        filters["project"] = project
    try:
        jql = build_jql(filters, cloud=client.cloud)
        found = client.search(jql, request_fields(jira_service.field_map(conn)), limit=10,
                              strict=not ISSUE_KEY_RE.match(text.upper()))
    except JiraError as exc:
        if exc.status == 400:
            return jsonify({"issues": []})  # недописанное слово или ключ несуществующей задачи — просто нет подсказок
        if exc.status is None:
            raise ApiError(400, "validation_error", str(exc)) from None
        raise _jira_failed(exc) from None
    fmap = jira_service.field_map(conn)
    return jsonify({"issues": [normalize_issue(k.upper(), f, conn["baseUrl"], fmap) for k, f in found]})


SOURCE_SECONDS = 120
SOURCE_LIMIT = 500
_source_cache = {}  # (план, JQL) → (ответ, до какого времени)


@bp.get("/plans/<int:plan_id>/jira/source")
@login_required
@limiter.limit("30 per minute", key_func=_user_key, exempt_when=lambda: request.args.get("refresh") != "1")
def plan_source(plan_id):
    """Новые задачи источника плана (проект и уточняющий JQL из настроек плана), которых в плане ещё нет:
    {configured, project, jql, total, newCount, new: [первые 20]}. Новые — незавершённые и не эпики (эпик в плане —
    группа). Только тем, кто правит план."""
    plan, _project, _level = _plan_with_access(plan_id, "edit")
    source = (plan.settings or {}).get("jiraSource") or {}
    project = source.get("project") or ""
    extra = (source.get("jql") or "").strip()
    conn = jira_service.get_connection()
    if not conn or not (project or extra):
        return jsonify({"configured": False, "newCount": 0, "new": []})
    linked = set(jira_service.plan_issue_keys(plan.id, conn))
    try:
        jql = build_jql({"project": project, "onlyOpen": True}) if project else ""
        if extra:
            # Уточняющий JQL — в скобках, порядок — свой: так условие сотрудника не меняет смысл ограничения проектом
            where = re.split(r"\s+ORDER\s+BY\s+", extra, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            jql = (jql.split(" ORDER BY ")[0] + f" AND ({where}) ORDER BY Rank ASC") if jql else \
                f"statusCategory != 3 AND ({where}) ORDER BY Rank ASC"
    except JiraError as exc:
        raise ApiError(400, "validation_error", str(exc)) from None
    cache_key = (plan.id, jql)
    cached = _source_cache.get(cache_key)
    if cached and cached[1] > time.monotonic() and request.args.get("refresh") != "1":
        found = cached[0]
    else:
        try:
            client = jira_service.make_client(detailed=current_user().role == "admin")
            db.session.rollback()
            found = [(k.upper(), f) for k, f in client.search(jql, request_fields(jira_service.field_map(conn)),
                                                              limit=SOURCE_LIMIT) if not is_epic(f)]
        except JiraError as exc:
            if exc.status == 400 and exc.detail:
                return jsonify({"configured": True, "project": project, "jql": extra, "error":
                                f"Jira не приняла запрос источника: {exc.detail}", "newCount": 0, "new": []})
            return jsonify({"configured": True, "project": project, "jql": extra, "error": str(exc), "newCount": 0,
                            "new": []})
        _source_cache[cache_key] = (found, time.monotonic() + SOURCE_SECONDS)
        if len(_source_cache) > 500:
            for key in [k for k, v in _source_cache.items() if v[1] < time.monotonic()]:
                _source_cache.pop(key, None)
    fmap = jira_service.field_map(conn)
    fresh = [(k, f) for k, f in found if k not in linked]
    return jsonify({"configured": True, "project": project, "jql": extra, "total": len(found),
                    "truncated": len(found) >= SOURCE_LIMIT, "newCount": len(fresh),
                    "new": [normalize_issue(k, f, conn["baseUrl"], fmap) for k, f in fresh[:20]], "error": None})
