"""Перенос базы «Пульса Проекта» 1.0.x (SQLite) в 0.2 (ADR-0005).

    python scripts/import_legacy.py /path/to/puls.db [--report report.md] [--replace] [--dry-run]

* исходные файлы не изменяются (копируются во временный каталог вместе с -wal/-shm);
* перенос идёт одной транзакцией в пустую базу; после переноса каждый план сверяется
  с исходной вкладкой — при любом расхождении транзакция откатывается;
* --dry-run — выполнить перенос и сверку, но откатить (репетиция без записи);
* --replace — очистить базу 0.2 перед переносом (только для повторной репетиции!).

Код возврата: 0 — успех, 2 — расхождения сверки, 1 — ошибка.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services.legacy_import import (  # noqa: E402
    LegacyImportError, LegacyImporter, _target_is_empty, _wipe_target, import_legacy_database, open_legacy,
    render_report,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Перенос базы Пульса Проекта 1.0.x")
    parser.add_argument("sqlite_path")
    parser.add_argument("--report", help="куда записать отчёт (Markdown)")
    parser.add_argument("--replace", action="store_true", help="очистить базу 0.2 перед переносом")
    parser.add_argument("--dry-run", action="store_true", help="перенести, сверить и откатить")
    args = parser.parse_args(argv)

    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        try:
            if args.dry_run:
                conn, tmp = open_legacy(args.sqlite_path)
                try:
                    if not _target_is_empty():
                        if not args.replace:
                            raise LegacyImportError("В базе 0.2 уже есть данные (для репетиции — --replace)")
                        _wipe_target()
                    importer = LegacyImporter(conn)
                    stats = importer.run()
                    report, errors = importer.report, importer.verify_errors
                finally:
                    db.session.rollback()
                    conn.close()
            else:
                stats, report, errors = import_legacy_database(args.sqlite_path, replace=args.replace)
        except LegacyImportError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
        text = render_report(stats, report, errors, args.sqlite_path)
        if args.dry_run:
            text = text.replace("# Отчёт о переносе", "# Репетиция переноса (без записи)", 1)
        print(text)
        if args.report:
            try:
                Path(args.report).write_text(text, encoding="utf-8")
            except OSError as exc:
                print(f"Внимание: отчёт не записан в {args.report}: {exc}", file=sys.stderr)
        return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
