"""«По Делу»: подключение (администратор), загрузка структуры и сотрудников, адрес для ссылок (ADR-0009).

* GET|PUT|DELETE /api/settings/podelu, POST /api/settings/podelu/test — подключение, только администратору;
* GET  /api/podelu — есть ли подключение и адрес «По Делу» (для ссылок на подразделения и сотрудников);
* GET|POST /api/org/sync/podelu — загрузка структуры и сотрудников из «По Делу»: сначала план без записи,
  затем применение одной транзакцией (как файлы и синхронизация с AD, ADR-0008).
"""

import re
from typing import Literal, Optional

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import BaseModel, Field

from app import audit
from app.extensions import db, limiter
from app.podelu_client import PodeluError, check_base_url, encrypt_password, rights
from app.security import ApiError, admin_required, current_user, login_required
from app.services import employee_transfer, org, org_transfer
from app.services import podelu as podelu_service
from app.services import podelu_sync
from app.sessions import revoke_user_sessions

bp = Blueprint("podelu", __name__, url_prefix="/api")


def _user_key():
    return f"user:{session.get('user_id')}"


def _failed(exc):
    return ApiError(502, "podelu_error", str(exc))


class ConnectionIn(BaseModel):
    baseUrl: str = Field(max_length=500)
    login: str = Field(default="", max_length=300)
    password: str = Field(default="", max_length=1000)
    acceptInvalidCerts: bool = False


def _form(payload):
    data = payload.model_dump()
    data["login"] = data["login"].strip()
    # Пароль вставляют из буфера — с переводом строки по краю; управляющие символы внутри — ошибка формы
    data["password"] = data["password"].strip("\r\n")
    if re.search(r"[\x00-\x1f\x7f]", data["password"] + data["login"]):
        raise ApiError(400, "validation_error", "Логин или пароль содержит недопустимые символы")
    try:
        data["baseUrl"] = check_base_url(data["baseUrl"])
    except PodeluError as exc:
        raise ApiError(400, "validation_error", str(exc)) from None
    if not data["login"]:
        raise ApiError(400, "validation_error", "Укажите логин учётной записи «По Делу»")
    return data


# ---------- администратор: «Интеграции» ----------

@bp.get("/settings/podelu")
@admin_required
def get_connection():
    return jsonify(podelu_service.admin_json(podelu_service.get_connection()))


@bp.put("/settings/podelu")
@admin_required
def put_connection():
    data = _form(ConnectionIn.model_validate(request.get_json(silent=True) or {}))
    existing = podelu_service.get_connection()
    password = data.pop("password")
    if not password:
        if not (existing and existing["passwordEncrypted"]):
            raise ApiError(400, "validation_error", "Укажите пароль учётной записи «По Делу»")
        # сохранённый пароль уходит только на тот адрес и для той учётной записи, для которых он был введён
        if existing["baseUrl"] != data["baseUrl"]:
            raise ApiError(400, "validation_error", "Адрес «По Делу» изменён — введите пароль заново")
        if existing["login"] != data["login"]:
            raise ApiError(400, "validation_error", "Логин изменён — введите пароль заново")
        if data["acceptInvalidCerts"] and not existing["acceptInvalidCerts"]:
            # без проверки сертификата сохранённый пароль мог бы уйти подменённому серверу — подтвердите его заново
            raise ApiError(400, "validation_error", "Без проверки сертификата введите пароль заново")
    data["passwordEncrypted"] = encrypt_password(current_app.secret_key, password) if password \
        else existing["passwordEncrypted"]
    podelu_service.save_connection(data)
    old = existing or podelu_service.DEFAULT_CONNECTION
    changed = [k for k in ("baseUrl", "login", "acceptInvalidCerts") if old.get(k) != data.get(k)]
    if password:
        changed.append("password")
    if changed:
        audit.record("Настройки", "update" if existing else "create", "По Делу", ", ".join(changed)[:100],
                     old.get("baseUrl") or "", data["baseUrl"])
    db.session.commit()
    return jsonify(podelu_service.admin_json(podelu_service.get_connection()))


@bp.delete("/settings/podelu")
@admin_required
def delete_connection():
    existing = podelu_service.get_connection()
    if existing:
        podelu_service.delete_connection()
        audit.record("Настройки", "delete", "По Делу", "", existing["baseUrl"], "")
        db.session.commit()
    return jsonify({"ok": True})


@bp.post("/settings/podelu/test")
@admin_required
def test_connection():
    """Вход и /api/auth/me: учётная запись и есть ли у неё права на выгрузку структуры и сотрудников."""
    payload = request.get_json(silent=True)
    override = _form(ConnectionIn.model_validate(payload)) if payload else None
    try:
        client = podelu_service.make_client(override=override)
    except PodeluError as exc:
        raise _failed(exc) from None
    db.session.rollback()  # к «По Делу» — без открытой транзакции базы
    try:
        with client:
            client.login()
            user = client.me()
    except PodeluError as exc:
        raise _failed(exc) from None
    allowed = rights(user)
    missing = [label for ok, label in ((allowed["structure"], "структуры"), (allowed["employees"], "сотрудников"))
               if not ok]
    problem = None
    if missing:
        problem = (f"Вход выполнен, но у учётной записи нет прав на выгрузку {' и '.join(missing)} — в «По Делу» "
                   "ей нужна роль «Администратор» или полномочия «Ведёт структуру компании» и «Ведёт учётные записи»")
    return jsonify({"ok": True, "user": {"name": str(user.get("name") or "")[:255], "login": str(user.get("login") or "")[:150],
                                         "role": str(user.get("globalRole") or "")[:20]},
                    "rights": {"structure": allowed["structure"], "employees": allowed["employees"]},
                    "problem": problem})


# ---------- сотрудники: адрес для ссылок ----------

@bp.get("/podelu")
@login_required
def status():
    return jsonify(podelu_service.public_json(podelu_service.get_connection()))


# ---------- загрузка структуры и сотрудников ----------

@bp.get("/org/sync/podelu")
@admin_required
def sync_status():
    conn = podelu_service.get_connection()
    return jsonify({"configured": bool(conn and conn["passwordEncrypted"]),
                    "baseUrl": conn["baseUrl"] if conn else None, "login": conn["login"] if conn else None})


class SyncIn(BaseModel):
    apply: bool = False
    applySettings: bool = True
    newUsers: Literal["active", "inactive", "file"] = "inactive"
    createMissing: bool = True
    photos: bool = True
    decisions: dict = Field(default_factory=dict)
    snapshot: Optional[dict] = None
    fingerprint: Optional[str] = Field(default=None, max_length=64)


def _fetch(options):
    try:
        client = podelu_service.make_client()
    except PodeluError as exc:
        if podelu_service.get_connection() is None:
            raise ApiError(409, "podelu_not_configured", str(exc)) from None
        raise _failed(exc) from None
    db.session.rollback()  # выгрузка может идти долго — без открытой транзакции базы
    try:
        return podelu_sync.fetch(client, photos=options.photos)
    except PodeluError as exc:
        raise _failed(exc) from None


def _format_failed(exc):
    return ApiError(502, "podelu_format", f"«По Делу» вернула данные, которые «Пульс» не разобрал: {exc}")


def _fetching():
    payload = request.get_json(silent=True) or {}
    return not payload.get("snapshot")


@bp.post("/org/sync/podelu")
@admin_required
@limiter.limit("20 per minute", key_func=_user_key, exempt_when=lambda: not _fetching())
def sync_run():
    """Без snapshot и apply — получить данные из «По Делу» и показать план (ответ содержит snapshot для
    пересчёта); со snapshot — пересчитать план с решениями по коллизиям без обращения к «По Делу»;
    apply — заново получить данные, сверить fingerprint с проверенным и применить одной транзакцией."""
    payload = SyncIn.model_validate(request.get_json(silent=True) or {})
    options = podelu_sync.Options(apply_settings=payload.applySettings, new_users=payload.newUsers,
                                  create_missing=payload.createMissing, photos=payload.photos)
    actor_id = current_user().id
    if payload.apply and not payload.fingerprint:
        raise ApiError(400, "validation_error", "Сначала проверьте план загрузки из «По Делу»")
    try:
        if payload.snapshot and not payload.apply:
            snapshot = podelu_sync.snapshot_from_json(payload.snapshot)
            return jsonify(podelu_sync.preview(snapshot, actor_id=actor_id, options=options, decisions=payload.decisions))
        snapshot = _fetch(options)
        if not payload.apply or snapshot.fingerprint() != payload.fingerprint:
            result = podelu_sync.preview(snapshot, actor_id=actor_id, options=options, decisions=payload.decisions)
            result["snapshot"] = snapshot.as_json()
            if not payload.apply:
                return jsonify(result)
            return jsonify({**result, "error": "podelu_changed",
                            "message": "Данные в «По Делу» изменились после проверки — посмотрите план ещё раз"}), 409
        result, applied, deactivated = podelu_sync.apply(snapshot, actor_id=actor_id, options=options,
                                                         decisions=payload.decisions)
    except podelu_sync.SnapshotError as exc:
        db.session.rollback()
        raise ApiError(400, "validation_error", str(exc)) from None
    except (org_transfer.StructureFormatError, employee_transfer.EmployeeFormatError) as exc:
        db.session.rollback()
        raise _format_failed(exc) from None
    if not applied:
        db.session.rollback()
        return jsonify({**result, "error": "import_has_errors",
                        "message": "Разберите коллизии и ошибки в плане"}), 409
    org.assert_consistent()  # итоговая структура без циклов (ADR-0008)
    db.session.commit()
    for user_id in dict.fromkeys(deactivated):
        revoke_user_sessions(user_id)
    current_app.logger.info("podelu sync applied: %s departments, %s employees",
                            result["structure"]["summary"]["departments"], result["employees"]["summary"]["records"])
    return jsonify(result)
