# Версии и release notes для администраторов

Документ содержит технические и эксплуатационные изменения. Пользовательский рассказ — в
[versions.md](versions.md).

## Версия 0.3.0 (Октябрь 2026 г.)

Интеграция с Jira ([ADR-0007](architecture/adr-0007-jira-integration.md)), структура компании, общая с
«По Делу» ([ADR-0008](architecture/adr-0008-company-structure.md)), руководства в приложении. Подробно —
[release notes](release-notes/v0.3.0.md).

### Обновление

- С 0.2.x: `sh deploy/update.sh v0.3.0` в каталоге установки (`/opt/puls`): копия базы, сборка, миграции при
  старте backend. Новые миграции: `0002_jira_issues`, `0003_org_structure`, `0004_department_access` — только
  новые таблицы и столбцы, данные не меняются. `GET /api/health/ready` после обновления:
  `{"schema":"0004_department_access","version":"0.3.0"}`.
- Новых обязательных переменных нет. Необязательные: `JIRA_ALLOWED_HOSTS`, `JIRA_CACHE_SECONDS` (300),
  `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` — если backend выходит к Jira через прокси.
- Новые зависимости backend: `requests`, `cryptography` (ставятся при сборке образа).

### Jira

- «Администрирование → Интеграции»: одно подключение на систему, как в burndown_tool — Jira Server/DC (логин
  и пароль или Personal Access Token) или Jira Cloud (email и API-токен), «Не проверять TLS-сертификат», поля
  дат. Токен хранится зашифрованным ключом из `SECRET_KEY`; при смене `SECRET_KEY` его нужно ввести заново.
- Учётная запись подключения определяет, какие задачи видны сотрудникам с доступом к плану: используйте
  сервисную учётную запись с правом только просмотра. «Пульс» в Jira не пишет.
- Данные задач кэшируются (`jira_issues`, 5 минут); если Jira недоступна, планы открываются с данными кэша.

### Структура компании

- «Администрирование → Структура»: подразделения, типы, руководители; «Сотрудники» — подразделение и
  руководитель сотрудника; «Импорт и экспорт» — файлы `podelu.org-structure`, `podelu.employees` (CSV, JSON,
  ZIP с фото), быстрый CSV и синхронизация с Active Directory по подключению «Вход через домен».
- «Доступы»: правила для подразделений — действуют на всю ветку структуры.
- Изменения структуры — в реестре изменений, раздел «Структура».

### Руководства

- `/guide` и `/guide/admin` (только администраторам); PDF и DOCX — файлы сборки с хэшем в имени; старая ссылка
  `/user-guide.pdf` ведёт на `/guide`.

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
- Попытки входа ограничены на учётную запись (`LOGIN_RATE_LIMIT`) и с запасом на адрес (`LOGIN_IP_RATE_LIMIT`);
  за HTTPS-прокси сервера — `TRUSTED_PROXY_HOPS=2`. Локальный администратор не связывается с каталогом LDAP.
- CSRF (`X-CSRF-Token`), `LOGIN_RATE_LIMIT` (по умолчанию `10 per minute;50 per hour`, счётчик в памяти каждого
  процесса), bcrypt; `SECRET_KEY` обязателен (≥ 32 символов).
- Роли: «Наблюдатель» ограничен просмотром на сервере; последнего активного администратора нельзя понизить или
  отключить ([ADR-0004](architecture/adr-0004-roles-and-access.md)).

### Конфигурация (`.env`)

`POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`, `SECRET_KEY`, `HTTP_PORT`, `HTTP_BIND`,
`SESSION_COOKIE_SECURE`, `SESSION_LIFETIME_DAYS`, `LOGIN_RATE_LIMIT`, `LOGIN_IP_RATE_LIMIT`, `TRUSTED_PROXY_HOPS`,
`SNAPSHOT_INTERVAL_MINUTES`, `ACCESS_LOG`,
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
