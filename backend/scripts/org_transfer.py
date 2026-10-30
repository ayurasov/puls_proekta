"""Импорт и экспорт структуры компании и сотрудников из командной строки — те же форматы и правила,
что на странице «Импорт и экспорт структуры» (и в todo-planner «По Делу», scripts/org_transfer.py).

Загрузки по умолчанию — проверка без записи: печатается план. --apply применяет его одной
транзакцией; при ошибках ничего не меняется. Коллизии сотрудников остаются как в системе — чтобы
взять значения файла, разберите их на странице «Импорт и экспорт».

    python scripts/org_transfer.py export-structure -o structure.json
    python scripts/org_transfer.py import-structure structure.json --actor admin --apply
    python scripts/org_transfer.py export-employees --format zip -o employees.zip
    python scripts/org_transfer.py import-employees employees.zip --actor admin --new-users inactive --apply
    python scripts/org_transfer.py import-csv people.csv --actor admin --create-missing --apply
    python scripts/org_transfer.py sync-ad --actor admin --create-missing --deactivate-missing --apply   # по расписанию
    python scripts/org_transfer.py sync-podelu --actor admin --create-missing --apply                    # по расписанию

sync-podelu — структура и сотрудники из «По Делу» по подключению из «Администрирование → Интеграции»
(ADR-0009): структура, затем сотрудники — одной транзакцией. --prefer-podelu в коллизиях берёт значения
«По Делу» (ФИО, должность, подразделение, руководитель, «первое лицо», руководимые подразделения,
активность, фото); роль, логин и email не меняются никогда. Запись, для которой нужно решение «тот же ли это
человек», — ошибка: разберите её один раз на странице «Импорт и экспорт → Из «По Делу»», дальше сотрудник
сопоставляется по идентификатору «По Делу».

Код возврата: 0 — успех, 1 — в плане есть ошибки (ничего не применено), 2 — файл не прочитан или каталог
(«По Делу») недоступен.
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import g  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import audit  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.podelu_client import PodeluError  # noqa: E402
from app.security import ApiError  # noqa: E402
from app.services import ad_sync, employee_transfer, org, org_import, org_transfer, podelu, podelu_sync  # noqa: E402
from app.sessions import revoke_user_sessions  # noqa: E402


def _write(path, data):
    if path:
        Path(path).write_bytes(data)
    else:
        sys.stdout.buffer.write(data)


def _actor(login):
    actor = db.session.execute(select(User).where(User.login == login)).scalar_one_or_none() if login else None
    if actor is None or actor.role != "admin" or not actor.is_active:
        raise SystemExit("error: --actor — логин активного администратора")
    return actor


def _read_employees(path):
    """(формат, содержимое, {ключ записи: SHA-256}, {ключ записи: bytes}) из CSV, JSON или ZIP."""
    source = Path(path)
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            content = archive.read("employees.json").decode("utf-8")
            photos = {}
            for record in employee_transfer.parse_json(content):
                if record.avatar and record.avatar in archive.namelist():
                    photos[employee_transfer.record_key(record)] = archive.read(record.avatar)
        return "json", content, {key: hashlib.sha256(raw).hexdigest() for key, raw in photos.items()}, photos
    raw = source.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")   # CSV из Excel
    return ("csv" if source.suffix.lower() in (".csv", ".txt") else "json"), text, {}, {}


def _parser():
    parser = argparse.ArgumentParser(description="Импорт и экспорт структуры компании и сотрудников")
    parser.add_argument("--json", action="store_true", help="печатать план целиком (JSON)")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("export-structure", help="структура и её настройки — JSON")
    command.add_argument("-o", "--output")
    command = commands.add_parser("import-structure", help="загрузить файл структуры")
    command.add_argument("file")
    command.add_argument("--no-settings", action="store_true", help="оставить настройки структуры этой системы")
    command = commands.add_parser("export-employees", help="сотрудники — CSV, JSON или ZIP")
    command.add_argument("--format", choices=("csv", "json", "zip"), default="zip")
    command.add_argument("--no-avatars", action="store_true")
    command.add_argument("--active-only", action="store_true")
    command.add_argument("--with-email-only", action="store_true", help="только сотрудники с email (для «По Делу»)")
    command.add_argument("-o", "--output")
    command = commands.add_parser("import-employees", help="загрузить сотрудников из CSV, JSON или ZIP")
    command.add_argument("file")
    command.add_argument("--new-users", choices=("active", "inactive", "file"), default="inactive")
    command = commands.add_parser("import-csv", help="быстрый импорт: подразделения и сотрудники одной таблицей")
    command.add_argument("file")
    command.add_argument("--create-missing", action="store_true")
    command = commands.add_parser("sync-ad", help="синхронизация с Active Directory")
    command.add_argument("--create-missing", action="store_true")
    command.add_argument("--deactivate-missing", action="store_true")
    command = commands.add_parser("sync-podelu", help="структура и сотрудники из «По Делу»")
    command.add_argument("--create-missing", action="store_true", help="создавать сотрудников, которых нет в системе")
    command.add_argument("--new-users", choices=("active", "inactive", "file"), default="inactive",
                         help="новые сотрудники: активными, неактивными или как в «По Делу»")
    command.add_argument("--prefer-podelu", action="store_true",
                         help="в коллизиях брать значения «По Делу» (кроме роли, логина и email)")
    command.add_argument("--no-settings", action="store_true", help="оставить настройки структуры этой системы")
    command.add_argument("--no-photos", action="store_true", help="не загружать фото сотрудников")
    for name in ("import-structure", "import-employees", "import-csv", "sync-ad", "sync-podelu"):
        sub = commands.choices[name]
        sub.add_argument("--actor", required=True, help="логин администратора — автор изменений в реестре")
        sub.add_argument("--apply", action="store_true", help="применить (без флага — только проверка)")
    return parser


def _export(args):
    if args.command == "export-structure":
        _write(args.output, json.dumps(org_transfer.export_structure(), ensure_ascii=False, indent=2).encode("utf-8"))
        return 0
    records = employee_transfer.export_records(include_inactive=not args.active_only,
                                               include_without_email=not args.with_email_only)
    if args.format == "csv":
        data = employee_transfer.export_csv(records).encode("utf-8")
    elif args.format == "json":
        data = json.dumps(employee_transfer.export_document(records), ensure_ascii=False, indent=2).encode("utf-8")
    else:
        data = employee_transfer.export_zip(records, with_avatars=not args.no_avatars)
    _write(args.output, data)
    return 0


def _problems(employees):
    problems = [f"{r['email'] or r['login']}: {'; '.join(r['errors'])}" for r in employees["records"] if r["errors"]]
    problems += [f"{r['email']}: тот же человек, что {r['identity']['email']}? Решите на странице «Импорт и экспорт»"
                 for r in employees["records"] if r["unresolved"]]
    return problems


def _sync_podelu(args, actor):
    """Структура и сотрудники из «По Делу»: план, при --apply — применение одной транзакцией."""
    options = podelu_sync.Options(apply_settings=not args.no_settings, new_users=args.new_users,
                                  create_missing=args.create_missing, photos=not args.no_photos)
    client = podelu.make_client()
    db.session.rollback()   # выгрузка из «По Делу» — без открытой транзакции базы
    snapshot = podelu_sync.fetch(client, photos=options.photos)
    plan = podelu_sync.preview(snapshot, actor_id=actor.id, options=options)
    decisions = podelu_sync.prefer_podelu_decisions(plan["employees"]) if args.prefer_podelu else {}
    if decisions:
        plan = podelu_sync.preview(snapshot, actor_id=actor.id, options=options, decisions=decisions)
    deactivated, applied = [], False
    if args.apply and plan["canApply"]:
        plan, applied, deactivated = podelu_sync.apply(snapshot, actor_id=actor.id, options=options, decisions=decisions)
        if applied:
            try:
                org.assert_consistent()   # итоговая структура без циклов (ADR-0008)
            except ApiError as error:
                plan = {**plan, "canApply": False, "structure": {**plan["structure"],
                                                                 "errors": [*plan["structure"]["errors"], error.message]}}
                applied, deactivated = False, []
    result = {**plan, "summary": {"structure": plan["structure"]["summary"], "employees": plan["employees"]["summary"]}}
    result.pop("snapshot", None)
    return result, applied, plan["structure"]["errors"] + _problems(plan["employees"]), deactivated


def _import(args, actor, avatar_limit):
    """(результат, применён ли, проблемы, id отключённых)."""
    if args.command == "sync-podelu":
        return _sync_podelu(args, actor)
    if args.command == "import-structure":
        bundle = Path(args.file).read_text(encoding="utf-8-sig")
        if args.apply:
            result, applied = org_transfer.apply_structure(bundle, apply_settings=not args.no_settings)
        else:
            result, applied = org_transfer.plan_structure(bundle, apply_settings=not args.no_settings).as_dict(), False
        return result, applied, result["errors"], []
    if args.command == "import-employees":
        fmt, content, hashes, photos = _read_employees(args.file)
        records = employee_transfer.parse_content(fmt, content)
        options = {"actor_id": actor.id, "new_users": args.new_users, "avatars": hashes}
        deactivated = []
        if args.apply:
            result, applied, ids, upload, deactivated = employee_transfer.apply_employees(records, **options)
            if applied and upload:
                employee_transfer.set_avatars({str(ids[key]): photos[key] for key in upload if key in photos},
                                              max_bytes=avatar_limit)
        else:
            result, applied = employee_transfer.plan_employees(records, **options).as_dict(), False
        return result, applied, _problems(result), deactivated
    if args.command == "import-csv":
        raw = Path(args.file).read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1251")
        rows, options = org_import.parse_csv(text), {"source": "csv", "create_missing": args.create_missing}
    else:
        rows, refs = ad_sync.load_rows()
        options = {"source": "ad", "create_missing": args.create_missing,
                   "deactivate_missing": args.deactivate_missing, "manager_refs": refs}
    deactivated = []
    if args.apply:
        result, applied, deactivated = org_import.apply_import(rows, actor_id=actor.id, **options)
    else:
        result, applied = org_import.plan_import(rows, actor_id=actor.id, **options).as_dict(), False
    problems = [f"строка {u['line']} ({u['email'] or u['login']}): {'; '.join(u['errors'])}" for u in result["users"] if u["errors"]]
    return result, applied, problems, deactivated


def run(app, argv=None, out=None):
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    if args.command.startswith("export-"):
        with app.app_context():
            return _export(args)
    # Изменения записываются в реестр от имени администратора — как из интерфейса
    with app.test_request_context():
        actor = _actor(args.actor)
        g.current_user = actor
        try:
            result, applied, problems, deactivated = _import(args, actor, app.config["AVATAR_MAX_BYTES"])
        except (org_transfer.StructureFormatError, employee_transfer.EmployeeFormatError,
                org_import.ImportFormatError, ad_sync.AdSyncError, PodeluError, OSError, KeyError, ValueError) as error:
            db.session.rollback()
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.apply and applied:
            source = {"sync-ad": "AD", "sync-podelu": "По Делу"}.get(args.command) or Path(args.file).name
            audit.record("Структура" if args.command != "import-employees" else "Пользователи", "import",
                         f"Командная строка: {args.command}", "", "", source)
            db.session.commit()
            for user_id in dict.fromkeys(deactivated):
                revoke_user_sessions(user_id)
        else:
            db.session.rollback()
        print(json.dumps(result if args.json else result["summary"], ensure_ascii=False, indent=2 if args.json else None),
              file=out)
        for problem in problems:
            print(f"  {problem}", file=out)
        if not result.get("canApply", True):
            print("не применено: исправьте ошибки выше" if args.apply else "есть ошибки — применить нельзя", file=sys.stderr)
            return 1
    return 0


def main(argv=None):
    from app import create_app

    return run(create_app(os.environ.get("FLASK_ENV", "production")), argv)


if __name__ == "__main__":
    raise SystemExit(main())
