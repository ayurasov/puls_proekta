"""Состояние системы (ADR-0014): администратору — всегда, остальным — с полномочием «Смотрит состояние системы»."""

from flask import Blueprint, jsonify, session

from app.extensions import db, limiter
from app.security import capability_required
from app.services import system_health

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
