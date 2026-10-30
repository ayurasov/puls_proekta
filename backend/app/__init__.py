"""Фабрика Flask-приложения JSON API «Пульса Проекта»."""

import logging

from flask import Flask, jsonify
from flask_wtf.csrf import CSRFError
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import csrf, db, limiter
from app.security import ApiError
from app.sessions import DbSessionInterface


def _validation_details(exc):
    details = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()) if part != "__root__")
        details.append({"field": loc, "message": err.get("msg", "")})
    return details


def _configure_error_handlers(app):
    @app.errorhandler(ApiError)
    def _api_error(exc):
        db.session.rollback()
        return exc.response()

    @app.errorhandler(ValidationError)
    def _validation_error(exc):
        db.session.rollback()
        details = _validation_details(exc)
        message = "Проверьте заполнение полей"
        if details and details[0]["message"]:
            message = details[0]["message"].removeprefix("Value error, ")
        return jsonify({"error": "validation_error", "message": message, "details": details}), 400

    @app.errorhandler(CSRFError)
    def _csrf_error(exc):
        db.session.rollback()
        return jsonify({"error": "csrf_error", "message": "Сессия устарела — обновите страницу"}), 400

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        db.session.rollback()
        messages = {
            404: "Не найдено",
            405: "Метод не поддерживается",
            413: "Слишком большой запрос",
            429: "Слишком много попыток — подождите минуту",
        }
        return jsonify({
            "error": exc.name.lower().replace(" ", "_"),
            "message": messages.get(exc.code, exc.description),
        }), exc.code

    @app.errorhandler(Exception)
    def _unhandled(exc):
        app.logger.exception("Unhandled exception during request")
        db.session.rollback()
        if app.config.get("TESTING"):
            raise exc
        return jsonify({"error": "internal_server_error", "message": "Внутренняя ошибка сервера"}), 500


def _register_blueprints(app):
    from app.api.access import bp as access_bp
    from app.api.audit import bp as audit_bp
    from app.api.auth import bp as auth_bp
    from app.api.avatars import bp as avatars_bp
    from app.api.calendar import bp as calendar_bp
    from app.api.directions import bp as directions_bp
    from app.api.health import bp as health_bp
    from app.api.jira import bp as jira_bp
    from app.api.org import bp as org_bp
    from app.api.org_transfer import bp as org_transfer_bp
    from app.api.plans import bp as plans_bp
    from app.api.projects import bp as projects_bp
    from app.api.settings import bp as settings_bp
    from app.api.users import bp as users_bp

    for bp in (health_bp, auth_bp, avatars_bp, users_bp, directions_bp, projects_bp, plans_bp,
               access_bp, audit_bp, settings_bp, calendar_bp, jira_bp, org_bp, org_transfer_bp):
        app.register_blueprint(bp)


def create_app(config_name=None, overrides=None):
    from config import get_config

    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    if overrides:
        app.config.update(overrides)
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    if app.config.get("ENV") == "production":
        hops = max(1, app.config.get("TRUSTED_PROXY_HOPS", 1))
        # nginx перезаписывает X-Forwarded-Proto и Host (одно значение), а X-Forwarded-For дополняет
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=1, x_host=1)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    app.session_interface = DbSessionInterface()

    _register_blueprints(app)
    _configure_error_handlers(app)

    from app.cli import register_cli

    register_cli(app)
    return app
