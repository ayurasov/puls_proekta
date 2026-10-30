# Версии и release notes для администраторов

Документ содержит технические и эксплуатационные изменения. Пользовательский рассказ — в
[versions.md](versions.md).

## Версия 0.2.0 (Октябрь 2026 г.)

Новая платформа: [ADR-0001](architecture/adr-0001-platform-and-stack.md) — [ADR-0006](architecture/adr-0006-native-gantt-editor.md).
Переход с 1.0.1 — [runbook](runbooks/v0.2.0-migration-from-1.0.1.md); подробности — [release notes](release-notes/v0.2.0.md);
что найдено в 1.0.1 — [ревизия](review/v0.2.0-revision-1.0.1.md); нагрузка — [отчёт](performance/loadtest-v0.2.0.md).

### Платформа

- Docker Compose: `db` (PostgreSQL 16, том `db_data`), `backend` (Flask, gunicorn sync, 2 × ядра + 1
  процессов), `frontend` (nginx: SPA, прокси `/api`, заголовки безопасности, CSP), `backup` (`pg_dump -Fc`
  ежедневно в `BACKUP_HOUR`, хранение `BACKUP_RETENTION_DAYS`); разовый профиль `migrate` — сервис
  `legacy-import`.
- Порт `HTTP_PORT=39663`, адрес привязки `HTTP_BIND`. HTTPS — внешним прокси (`Caddyfile.example` или nginx
  сервера), затем `SESSION_COOKIE_SECURE=true`.
- Миграции Alembic применяются при старте backend (`AUTO_MIGRATE=true`); схема — `0001_initial`, в том числе
  официальные дни производственного календаря 2025–2027.

### Данные

- Нормализованная модель: проекты → планы → строки, пометки, исполнители, комментарии, журнал `plan_events`,
  снимки `plan_snapshots`; правки — пакеты операций `POST /api/plans/<id>/ops` под блокировкой строки плана,
  по полям, с конфликтами и проверкой целостности ([ADR-0002](architecture/adr-0002-data-model-and-concurrency.md)).
- Автоснимки плана не чаще `SNAPSHOT_INTERVAL_MINUTES` (30) при правках; хранение: все за 2 суток, по одному в
  день за 60 дней, затем по одному в неделю. Ручные версии не удаляются.
- Удаление — в корзину (проекты — только администратор), удаление навсегда — только из корзины администратором.

### Перенос 1.0.1

- `docker compose --profile migrate run --rm legacy-import` переносит `./import/puls.db` (+ `-wal`, `-shm`) в
  пустую базу одной транзакцией со сверкой каждого плана; отчёт — `./import/import-report.md`; код 2 —
  расхождения, ничего не записано. Репетиция — `… legacy-import python scripts/import_legacy.py /import/puls.db --dry-run --report /import/rehearsal-report.md`.
- Сохраняются идентификаторы сотрудников, направлений и проектов, хэши паролей, фото, весь реестр. Дубли
  правил доступа объединяются, пробелы в ФИО/email/должностях нормализуются. Сессии не переносятся.

### Безопасность

- Сессии в `user_sessions` (SHA-256 токена), cookie `puls_session`; отключение и смена/сброс пароля завершают
  сессии; `flask --app wsgi purge-sessions` — очистка истёкших.
- CSRF (`X-CSRF-Token`), `LOGIN_RATE_LIMIT` (по умолчанию `10 per minute;50 per hour`, счётчик в памяти каждого
  процесса), bcrypt; `SECRET_KEY` обязателен (≥ 32 символов).
- Роли: «Наблюдатель» ограничен просмотром на сервере; последнего активного администратора нельзя понизить или
  отключить ([ADR-0004](architecture/adr-0004-roles-and-access.md)).

### Конфигурация (`.env`)

`POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`, `SECRET_KEY`, `HTTP_PORT`, `HTTP_BIND`,
`SESSION_COOKIE_SECURE`, `SESSION_LIFETIME_DAYS`, `LOGIN_RATE_LIMIT`, `SNAPSHOT_INTERVAL_MINUTES`,
`BOOTSTRAP_ADMIN_PASSWORD` (только новая установка без переноса), `BACKUP_DIR`, `BACKUP_HOUR`,
`BACKUP_RETENTION_DAYS`, `TZ`, `GUNICORN_WORKERS`. Описание — руководство по развёртыванию.

### Обслуживание

- `sh deploy/update.sh [версия]` — копия `pre-update`, получение версии, пересборка, проверка готовности,
  подсказка отката.
- `docker compose exec backup sh /usr/local/bin/backup.sh now|list|restore <файл>` — копия, список,
  восстановление (с копией `pre-restore`; требует остановленного backend).
- `docker compose exec backend flask --app wsgi reset-password <логин>` — сброс пароля;
  `bootstrap-admin`, `calendar-seed`, `purge-sessions`.
- Проверки: `GET /api/health/live`, `GET /api/health/ready` (схема и версия), `/healthz` nginx.

### Проверка после обновления

- `curl http://127.0.0.1:39663/api/health/ready` — `{"status":"ok","schema":"0001_initial","version":"0.2.0"}`;
- вход администратора, открытие крупного проекта, «История плана → Версии» содержит «Перенос из 1.0.1»;
- `backup.sh list` — есть копия после переноса.
