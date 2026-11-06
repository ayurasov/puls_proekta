"""Состояние системы (ADR-0014) и резервные копии (ADR-0015).

Состояние — администратору всегда, остальным — с полномочием «Смотрит состояние системы». Резервные копии видит и
тот, у кого есть «Ведёт резервное копирование»; расписание, хранение и копию по запросу меняет только он (и
администратор)."""

import re
from typing import Optional

from flask import Blueprint, jsonify, request, session
from pydantic import BaseModel, Field, field_validator

from app import audit
from app.extensions import db, limiter
from app.security import ApiError, capability_required, current_user
from app.services import backups, system_health

bp = Blueprint("system", __name__, url_prefix="/api/system")


def _user_key():
    return f"user:{session.get('user_id')}"


@bp.get("/health")
@capability_required("system.health", message="Состояние системы видит администратор или сотрудник с полномочием "
                                              "«Смотрит состояние системы»")
def health():
    return jsonify(system_health.overview())


@bp.post("/health/check")
@capability_required("system.health", message="Проверить подключения может администратор или сотрудник с "
                                              "полномочием «Смотрит состояние системы»")
@limiter.limit("6 per minute", key_func=_user_key)
def check():
    """Проверить подключения сейчас: Jira, «По Делу» и каталог — сохранёнными настройками, без их показа."""
    from app.jira_client import JiraError
    from app.ldap_auth import get_ldap_config, test_connection
    from app.podelu_client import PodeluError
    from app.services import jira as jira_service
    from app.services import podelu as podelu_service
    from app.services import podelu_links as links

    checks = []
    db.session.rollback()  # к внешним системам — без открытой транзакции базы

    def run(key, title, action):
        try:
            action()
            checks.append({"key": key, "title": title, "ok": True, "message": ""})
        except (JiraError, PodeluError) as exc:
            checks.append({"key": key, "title": title, "ok": False, "message": str(exc)[:300]})

    if jira_service.get_connection():
        run("jira", "Jira", lambda: jira_service.make_client().myself())
    if podelu_service.get_connection():
        def structure():
            with podelu_service.make_client() as client:
                client.login()
                client.me()
        run("podelu", "«По Делу»: структура и сотрудники", structure)
    if links.ready() or (links.get_settings().get("baseUrl") and links.get_settings().get("keyEncrypted")):
        run("podelu_links", "«По Делу»: ссылки из задач", lambda: links.make_client()[0].ping())
    config = get_ldap_config()
    if config.get("enabled"):
        result = test_connection(config)
        system_health.note("ldap", result.get("ok"), result.get("error") or "")
        checks.append({"key": "ldap", "title": "Вход через домен", "ok": bool(result.get("ok")),
                       "message": result.get("error") or ""})
    system_health.flush()
    return jsonify({**system_health.overview(), "checks": checks})


# ---------------------------------------------------------------- резервные копии (ADR-0015)

class BackupSettingsIn(BaseModel):
    enabled: bool = True
    time: str = Field(default="03:00")
    days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7], max_length=7)
    retentionDays: int = Field(default=30, ge=1, le=365)
    keepManualDays: Optional[int] = Field(default=90, ge=0, le=3650)

    @field_validator("time")
    @classmethod
    def _time(cls, value):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value or ""):
            raise ValueError("Время копии — ЧЧ:ММ, например 03:00")
        return value

    @field_validator("days")
    @classmethod
    def _days(cls, value):
        if any(d < 1 or d > 7 for d in value):
            raise ValueError("Дни недели — от 1 (понедельник) до 7 (воскресенье)")
        return sorted(set(value))


VIEW = ("system.health", "backups.manage")


@bp.get("/backups")
@capability_required(*VIEW, message="Резервные копии видит администратор или сотрудник с полномочием «Смотрит "
                                     "состояние системы» или «Ведёт резервное копирование»")
def backups_overview():
    from app.security import has_capability

    return jsonify({**backups.overview(), "canManage": has_capability(current_user(), "backups.manage")})


@bp.put("/backups/settings")
@capability_required("backups.manage", message="Расписание копий меняет администратор или сотрудник с полномочием "
                                               "«Ведёт резервное копирование»")
def backups_settings():
    payload = BackupSettingsIn.model_validate(request.get_json(silent=True) or {})
    if payload.enabled and not payload.days:
        raise ApiError(400, "validation_error", "Отметьте хотя бы один день недели или выключите копии по расписанию")
    values = payload.model_dump()
    values["keepManualDays"] = values["keepManualDays"] or 0
    old = backups.stored_settings()
    if old != values:
        backups.save_settings(values)
        audit.record("Настройки", "update" if old else "create", "Резервные копии", "backup",
                     _settings_text(old) if old else "из настроек сервера", _settings_text(values))
        db.session.commit()
    return jsonify({**backups.overview(), "canManage": True})


@bp.post("/backups/run")
@capability_required("backups.manage", message="Копию по запросу делает администратор или сотрудник с полномочием "
                                               "«Ведёт резервное копирование»")
@limiter.limit("3 per minute", key_func=_user_key)
def backups_run():
    """«Сделать копию сейчас»: запрос для службы копий — она выполнит его в течение минуты."""
    state = backups.overview()
    if state["request"]:
        raise ApiError(409, "backup_requested", "Копия уже запрошена — служба сделает её в течение минуты")
    if state["running"]:
        raise ApiError(409, "backup_running", "Копия уже делается — дождитесь её окончания")
    user = current_user()
    backups.put_request(user.name)
    audit.record("Настройки", "create", "Резервные копии", "backup", "", "копия по запросу")
    db.session.commit()
    return jsonify({**backups.overview(), "canManage": True}), 202


def _settings_text(values):
    keep = values.get("keepManualDays") or 0
    return (f"{backups.schedule_text(values)}; хранить {values.get('retentionDays')} дн., "
            f"вручную — {f'{keep} дн.' if keep else 'всегда'}")
