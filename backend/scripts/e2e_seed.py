"""Подготовка базы для e2e-тестов: миграции, перенос снимка 1.0.1, тестовые учётные записи.

    SQLALCHEMY_DATABASE_URI=sqlite:////tmp/e2e.db python scripts/e2e_seed.py ../legacy/snapshot/puls-2026-10-30.db

Пароли тестовых учётных записей — только для e2e-стенда.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import AccessRule, Project, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services.legacy_import import import_legacy_database  # noqa: E402
from scripts import migrate_on_start  # noqa: E402

PASSWORD = "e2e-password-1"


def main(snapshot):
    migrate_on_start.main()
    app = create_app("e2e", {"BCRYPT_ROUNDS": 4})
    with app.app_context():
        stats, _report, errors = import_legacy_database(snapshot, replace=True)
        if errors:
            raise SystemExit("\n".join(errors))
        admin = db.session.execute(select(User).where(User.login == "admin")).scalar_one()
        admin.password_hash = hash_password(PASSWORD)
        project = db.session.execute(select(Project).where(Project.title == "ИСУП МО - реализация договоров")).scalar_one()
        for login, role, level in (("e2e.editor", "editor", "edit"), ("e2e.viewer", "viewer", "view")):
            user = User(login=login, name=f"E2E {role}", role=role, password_hash=hash_password(PASSWORD))
            db.session.add(user)
            db.session.flush()
            db.session.add(AccessRule(user_id=user.id, project_id=project.id, level=level))
        db.session.commit()
        print(f"e2e seed: {stats.get('plans')} планов, {stats.get('items_task')} задач")


if __name__ == "__main__":
    main(sys.argv[1])
