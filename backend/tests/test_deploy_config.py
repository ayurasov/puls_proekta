"""Настройки развёртывания (0.3.1): перенос 1.0.x не зависит от BOOTSTRAP_ADMIN_PASSWORD."""

from pathlib import Path

import pytest

from app.cli import ensure_bootstrap_admin
from app.extensions import db
from app.models import User

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_import_never_creates_bootstrap_admin():
    """Перенос запускается тем же входом контейнера, что и backend: администратор из BOOTSTRAP_ADMIN_PASSWORD
    в пустой базе сделал бы её непустой, и перенос отказался бы, а короткий пароль ронял перенос."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose[compose.index("  legacy-import:"):]
    block = block[:block.index("\nvolumes:")]
    assert "<<: *backend-env" in block and 'BOOTSTRAP_ADMIN_PASSWORD: ""' in block


def test_short_bootstrap_password_explains_both_ways(app):
    app.config["BOOTSTRAP_ADMIN_PASSWORD"] = "123"
    with app.app_context():
        db.session.query(User).delete()
        db.session.commit()
    with pytest.raises(RuntimeError) as exc:
        ensure_bootstrap_admin(app)
    message = str(exc.value)
    assert "не короче" in message and "оставьте BOOTSTRAP_ADMIN_PASSWORD пустым" in message and "legacy-import" in message
    app.config["BOOTSTRAP_ADMIN_PASSWORD"] = "достаточно-длинный"
    assert ensure_bootstrap_admin(app) is True
    assert ensure_bootstrap_admin(app) is False  # база уже не пустая
