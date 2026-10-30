"""Jira: подключение (администратор) и задачи, привязанные к строкам планов (ADR-0007)."""

import re
from typing import Literal, Optional

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import BaseModel, Field

from app import audit
from app.api.plans import _plan_with_access
from app.extensions import db, limiter
from app.jira_client import (
    FIELD_ID_RE, JiraError, check_base_url, encrypt_token, normalize_issue, parse_issue_keys, request_fields,
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
    changed = [k for k in ("baseUrl", "instanceType", "login", "acceptInvalidCerts", "startField", "endField")
               if old.get(k) != data.get(k)]
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
    return jsonify({"ok": True, "user": user, **info})


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
    rows = jira_service.plan_issue_keys(plan.id, conn["baseUrl"])
    issues, error, fetched_at = {}, None, None
    if rows:
        try:
            issues, error, fetched_at = jira_service.fetch_issues(
                list(rows), refresh=request.args.get("refresh") == "1", detailed=current_user().role == "admin")
        except JiraError as exc:
            error = str(exc)
    return jsonify({"connected": True, "issues": issues, "rows": rows, "error": error,
                    "fetchedAt": fetched_at.isoformat() + "Z" if fetched_at else None})


class SearchIn(BaseModel):
    planId: int
    keys: str = Field(default="", max_length=20000)
    jql: str = Field(default="", max_length=2000)
    limit: Optional[int] = Field(default=SEARCH_MAX, ge=1, le=SEARCH_MAX)
    refresh: bool = False
    # Разложить по эпикам: у задач — ключ эпика, в ответе — эпики (название, ссылка, статус)
    epics: bool = False


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
    if not keys and not payload.jql.strip():
        raise ApiError(400, "validation_error", "Укажите ключи задач или запрос JQL")
    detailed = user.role == "admin"
    if payload.epics:
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
    """Поиск для «Задач из Jira» с эпиками: поле «Epic Link» (Server/DC) или родитель (Cloud), эпики — отдельным
    запросом. Кэш задач строк не используется: в нём нет полей эпика."""
    try:
        client = jira_service.make_client(detailed=detailed)
        fmap = {**jira_service.field_map(conn), "epicField": jira_service.epic_link_field(client)}
        fields = request_fields(fmap, epics=True)
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
            "error": None, "epics": {k: {"key": k, "summary": e["summary"], "url": e["url"], "status": e["status"],
                                         "statusCategory": e["statusCategory"]} for k, e in epics.items()}}
