"""Команды обслуживания: flask --app wsgi <команда>."""

import click
from sqlalchemy import func, select

from app.extensions import db
from app.models import CalendarDay, User
from app.passwords import hash_password, password_problem
from app.sessions import purge_expired_sessions


def ensure_bootstrap_admin(app):
    """Пустая база (новая установка без переноса): создать администратора из BOOTSTRAP_ADMIN_PASSWORD."""
    with app.app_context():
        if db.session.execute(select(func.count()).select_from(User)).scalar_one() > 0:
            return False
        password = app.config.get("BOOTSTRAP_ADMIN_PASSWORD") or ""
        if not password:
            print("База пустая: задайте BOOTSTRAP_ADMIN_PASSWORD или перенесите данные 1.0.x (scripts/import_legacy.py)")
            return False
        problem = password_problem(password)
        if problem:
            raise RuntimeError(f"BOOTSTRAP_ADMIN_PASSWORD: {problem}")
        db.session.add(User(login=app.config["BOOTSTRAP_ADMIN_LOGIN"], name="Администратор", role="admin",
                            password_hash=hash_password(password), auth_source="local"))
        db.session.commit()
        print(f"Создан администратор {app.config['BOOTSTRAP_ADMIN_LOGIN']}")
        return True


def seed_calendar():
    from app.calendar_data import official_rows

    added = 0
    for row in official_rows():
        existing = db.session.get(CalendarDay, row["day"])
        if existing is None:
            db.session.add(CalendarDay(**row))
            added += 1
    db.session.commit()
    return added


def register_cli(app):
    @app.cli.command("bootstrap-admin")
    def bootstrap_admin():
        ensure_bootstrap_admin(app)

    @app.cli.command("calendar-seed")
    def calendar_seed():
        """Добавить официальные дни производственного календаря (свои правки не трогаются)."""
        click.echo(f"Добавлено дней: {seed_calendar()}")

    @app.cli.command("purge-sessions")
    def purge_sessions():
        purge_expired_sessions()
        click.echo("Истёкшие сессии удалены")

    @app.cli.command("reset-password")
    @click.argument("login")
    @click.password_option()
    def reset_password(login, password):
        """Сбросить пароль сотрудника (если администратор забыл свой пароль)."""
        user = db.session.execute(select(User).where(User.login == login)).scalar_one_or_none()
        if user is None:
            raise click.ClickException("Сотрудник не найден")
        problem = password_problem(password)
        if problem:
            raise click.ClickException(problem)
        user.password_hash = hash_password(password)
        user.auth_source = "local"
        user.is_active = True
        db.session.commit()
        from app.sessions import revoke_user_sessions

        revoke_user_sessions(user.id)
        click.echo("Пароль изменён, сессии сотрудника завершены")
