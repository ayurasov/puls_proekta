"""Импорт и экспорт структуры компании и сотрудников (ADR-0008) — форматы общие с todo-planner «По Делу».

* GET  /api/org/export, POST /api/org/import/structure — файл структуры podelu.org-structure;
* POST /api/org/import/csv — быстрый импорт: подразделения и сотрудники одной таблицей;
* GET|POST /api/org/sync/ad, PUT /api/org/sync/ad/settings — синхронизация с Active Directory;
* GET  /api/users/export, POST /api/users/import, POST /api/users/import/avatars — сотрудники
  podelu.employees (CSV, JSON, ZIP с фото).

Всё — только администратору. Загрузка везде в два шага: план без записи в БД (apply=false), затем
применение одной транзакцией (apply=true); если в плане есть ошибки, применение отвечает 409
и ничего не меняет.
"""

import json
import re
from datetime import datetime, timezone
from typing import Literal

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, Field, field_validator

from app import audit
from app.extensions import db
from app.ldap_auth import effective_mode, get_ldap_config
from app.security import ApiError, admin_required, current_user
from app.services import org, ad_sync, employee_transfer, org_import, org_transfer
from app.sessions import revoke_user_sessions

bp = Blueprint("org_transfer", __name__, url_prefix="/api")

MAX_CSV_CHARS = 5_000_000
MAX_EMPLOYEES_CHARS = 10_000_000


def _bad(message, field_name=None):
    return ApiError(400, "validation_error", message,
                    [{"field": field_name, "message": message}] if field_name else None)


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _download(body, mimetype, filename):
    return Response(body, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


def _rejected(result, message):
    db.session.rollback()
    return jsonify({**result, "error": "import_has_errors", "message": message}), 409


def _revoke(user_ids):
    for user_id in dict.fromkeys(user_ids):
        revoke_user_sessions(user_id)


# ---------------------------------------------------------------- структура (JSON)
@bp.get("/org/export")
@admin_required
def export_structure():
    body = json.dumps(org_transfer.export_structure(), ensure_ascii=False, indent=2)
    return _download(body.encode("utf-8"), "application/json", f"puls-structure-{_stamp()}.json")


@bp.post("/org/import/structure")
@admin_required
def import_structure():
    payload = request.get_json(silent=True) or {}
    bundle = payload.get("bundle")
    apply_settings = payload.get("applySettings", True) is not False
    try:
        if not payload.get("apply"):
            plan = org_transfer.plan_structure(bundle, apply_settings=apply_settings).as_dict()
            return jsonify({**plan, "settingsApplied": apply_settings})
        org.lock_org()
        result, applied = org_transfer.apply_structure(bundle, apply_settings=apply_settings)
    except org_transfer.StructureFormatError as error:
        raise _bad(str(error), "bundle")
    if not applied:
        return _rejected({**result, "settingsApplied": apply_settings}, "Исправьте ошибки в файле")
    audit.record("Структура", "import", "Файл структуры", "", "",
                 f"подразделений: новых {result['summary']['create']}, изменено {result['summary']['update']}")
    org.assert_consistent()  # итоговая структура без циклов (ADR-0008)
    db.session.commit()
    current_app.logger.info("org structure imported: %s departments", result["summary"]["departments"])
    return jsonify({**result, "applied": True, "settingsApplied": apply_settings})


# ---------------------------------------------------------------- быстрый импорт CSV
@bp.post("/org/import/csv")
@admin_required
def import_csv():
    payload = request.get_json(silent=True) or {}
    text = payload.get("csv")
    if not isinstance(text, str) or len(text) > MAX_CSV_CHARS:
        raise _bad("csv: текст файла до 5 МБ", "csv")
    create_missing, apply = bool(payload.get("createMissing")), bool(payload.get("apply"))
    try:
        rows = org_import.parse_csv(text)
    except org_import.ImportFormatError as error:
        raise _bad(str(error), "csv")
    actor_id = current_user().id
    if not apply:
        return jsonify(org_import.plan_import(rows, source="csv", create_missing=create_missing, actor_id=actor_id).as_dict())
    org.lock_org()
    result, applied, deactivated = org_import.apply_import(rows, actor_id=actor_id, source="csv",
                                                           create_missing=create_missing)
    if not applied:
        return _rejected(result, "Исправьте ошибки в файле")
    audit.record("Структура", "import", "Быстрый импорт CSV", "", "", _csv_summary(result))
    org.assert_consistent()  # итоговая структура без циклов (ADR-0008)
    db.session.commit()
    _revoke(deactivated)
    return jsonify({**result, "applied": True})


def _csv_summary(result):
    s = result["summary"]
    return (f"строк {s['rows']}: новых подразделений {s['createDepartments']}, новых сотрудников {s['createUsers']}, "
            f"с изменениями {s['updateUsers']}, отключено {s['deactivateUsers']}")


# ---------------------------------------------------------------- Active Directory
class AdSyncOptionsIn(BaseModel):
    userFilter: str = Field(default=ad_sync.DEFAULT_FILTER, min_length=3, max_length=1000)
    pathSource: Literal["attributes", "ou"] = "attributes"
    pathAttributes: list[str] = Field(default_factory=lambda: list(ad_sync.DEFAULT_PATH_ATTRIBUTES), max_length=10)
    detectHeads: bool = True

    @field_validator("userFilter")
    @classmethod
    def _filter(cls, value):
        value = value.strip()
        if not (value.startswith("(") and value.endswith(")")):
            raise ValueError("Фильтр LDAP — в скобках, например (&(objectClass=user)(mail=*))")
        return value

    @field_validator("pathAttributes")
    @classmethod
    def _attributes(cls, value):
        result = [item.strip() for item in value if item and item.strip()]
        for item in result:
            if not re.match(r"^[A-Za-z][A-Za-z0-9-]{0,63}$", item):
                raise ValueError(f"Атрибут «{item}»: латиница, цифры и дефис")
        return list(dict.fromkeys(result))


def _ad_status():
    config = ad_sync.current_config()
    ldap = get_ldap_config()
    return {
        "configured": config is not None,
        "ldap3Available": ad_sync.ldap3_available(),
        "config": config.describe() if config else None,
        "options": ad_sync.get_options(),
        # Вход по учётной записи домена: те же настройки подключения
        "login": {"enabled": bool(ldap.get("enabled")), "mode": effective_mode(ldap), "url": ldap.get("serverUrl") or ""},
    }


@bp.get("/org/sync/ad")
@admin_required
def ad_status():
    return jsonify(_ad_status())


@bp.put("/org/sync/ad/settings")
@admin_required
def ad_settings():
    payload = AdSyncOptionsIn.model_validate(request.get_json(silent=True) or {})
    if payload.pathSource == "attributes" and not payload.pathAttributes:
        raise _bad("Укажите хотя бы один атрибут пути подразделения", "pathAttributes")
    old = ad_sync.get_options()
    options = payload.model_dump()
    ad_sync.save_options(options)
    changed = [key for key in options if old.get(key) != options[key]]
    if changed:
        audit.record("Настройки", "update", "Синхронизация с AD", ", ".join(changed), "", "изменено")
    db.session.commit()
    return jsonify(_ad_status())


@bp.post("/org/sync/ad")
@admin_required
def ad_sync_run():
    payload = request.get_json(silent=True) or {}
    create_missing, deactivate_missing = bool(payload.get("createMissing")), bool(payload.get("deactivateMissing"))
    try:
        rows, refs = ad_sync.load_rows()
    except ad_sync.AdSyncError as error:
        raise ApiError(503, "ad_unavailable", str(error))
    options = {"source": "ad", "create_missing": create_missing, "deactivate_missing": deactivate_missing,
               "manager_refs": refs}
    actor_id = current_user().id
    if not payload.get("apply"):
        return jsonify(org_import.plan_import(rows, actor_id=actor_id, **options).as_dict())
    org.lock_org()
    result, applied, deactivated = org_import.apply_import(rows, actor_id=actor_id, **options)
    if not applied:
        return _rejected(result, "В данных каталога есть ошибки")
    audit.record("Структура", "import", "Синхронизация с AD", "", "", _csv_summary(result))
    org.assert_consistent()  # итоговая структура без циклов (ADR-0008)
    db.session.commit()
    _revoke(deactivated)
    return jsonify({**result, "applied": True})


# ---------------------------------------------------------------- сотрудники
@bp.get("/users/export")
@admin_required
def export_users():
    fmt = request.args.get("format", "csv")
    if fmt not in ("csv", "json", "zip"):
        raise _bad("Формат выгрузки: csv, json или zip", "format")
    records = employee_transfer.export_records(include_inactive=request.args.get("inactive", "1") != "0",
                                               include_without_email=request.args.get("withoutEmail", "1") != "0")
    if fmt == "csv":
        body, mimetype = employee_transfer.export_csv(records).encode("utf-8"), "text/csv; charset=utf-8"
    elif fmt == "json":
        body = json.dumps(employee_transfer.export_document(records), ensure_ascii=False, indent=2).encode("utf-8")
        mimetype = "application/json"
    else:
        body = employee_transfer.export_zip(records, with_avatars=request.args.get("avatars", "1") != "0")
        mimetype = "application/zip"
    return _download(body, mimetype, f"puls-employees-{_stamp()}.{fmt}")


@bp.post("/users/import")
@admin_required
def import_users():
    payload = request.get_json(silent=True) or {}
    content = payload.get("content")
    if not isinstance(content, str) or len(content) > MAX_EMPLOYEES_CHARS:
        raise _bad("Содержимое файла — до 10 МБ", "content")
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    new_users = options.get("newUsers", "active")
    if new_users not in ("active", "inactive", "file"):
        raise _bad("Новые сотрудники: active, inactive или file", "options.newUsers")
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), dict) else {}
    # Фото из ZIP: {ключ записи (email): SHA-256} — по хешу сервер отличает то же фото от нового
    raw_avatars = payload.get("avatars")
    if isinstance(raw_avatars, dict):
        avatars = {key: digest for key, digest in raw_avatars.items() if isinstance(key, str) and isinstance(digest, str)}
    else:
        avatars = [key for key in raw_avatars or [] if isinstance(key, str)]
    try:
        records = employee_transfer.parse_content(payload.get("format"), content)
    except employee_transfer.EmployeeFormatError as error:
        raise _bad(str(error), "content")
    actor = current_user()
    if not payload.get("apply"):
        plan = employee_transfer.plan_employees(records, actor_id=actor.id, new_users=new_users, decisions=decisions,
                                                avatars=avatars)
        return jsonify(plan.as_dict())
    org.lock_org()
    result, applied, ids, upload, deactivated = employee_transfer.apply_employees(
        records, actor_id=actor.id, new_users=new_users, decisions=decisions, avatars=avatars)
    if not applied:
        return _rejected(result, "Разберите коллизии и исправьте ошибки")
    s = result["summary"]
    audit.record("Пользователи", "import", "Загрузка сотрудников из файла", "", "",
                 f"записей {s['records']}: новых {s['create']}, обновлено {s['update']}")
    org.assert_consistent()  # итоговая структура без циклов (ADR-0008)
    db.session.commit()
    _revoke(deactivated)
    return jsonify({**result, "applied": True, "ids": ids, "avatarUploads": upload})


@bp.post("/users/import/avatars")
@admin_required
def import_avatars():
    """Фото после загрузки сотрудников: имя поля формы — id сотрудника, файл — фото."""
    max_bytes = current_app.config["AVATAR_MAX_BYTES"]
    files = {name: storage.read(max_bytes + 1) for name, storage in request.files.items()}
    if not files:
        raise _bad("Файлы не переданы", "files")
    errors, saved = employee_transfer.set_avatars(files, max_bytes=max_bytes)
    db.session.commit()
    return jsonify({"saved": saved, "errors": errors})

