"""Хэши паролей.

Пароли из 1.0.x хранятся в bcrypt ($2a$/$2b$, cost 12) — они проверяются как есть,
сотрудникам не нужно менять пароль после перехода. Новые пароли тоже хэшируются bcrypt.
"""

import bcrypt
from flask import current_app, has_app_context

MIN_PASSWORD_LENGTH = 8


def hash_password(password):
    rounds = current_app.config.get("BCRYPT_ROUNDS", 12) if has_app_context() else 12
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("ascii")


def verify_password(password, password_hash):
    if not password or not password_hash:
        return False
    if not password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def password_problem(password):
    """Текст ошибки для слабого пароля или None."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"
    if len(password) > 200:
        return "Пароль слишком длинный"
    return None
