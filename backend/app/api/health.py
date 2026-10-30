"""Проверки готовности и версия."""

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from app.extensions import db

bp = Blueprint("health", __name__, url_prefix="/api")


@bp.get("/health/live")
def live():
    return jsonify({"status": "ok"})


@bp.get("/health/ready")
def ready():
    try:
        db.session.execute(text("SELECT 1"))
        revision = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return jsonify({"status": "unavailable"}), 503
    return jsonify({"status": "ok", "schema": revision, "version": current_app.config["APP_VERSION"]})


@bp.get("/meta")
def meta():
    return jsonify({"version": current_app.config["APP_VERSION"], "name": "Пульс Проекта"})
