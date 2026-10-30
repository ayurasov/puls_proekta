# Пульс Проекта — v0.2.0

Корпоративная on-premise система планирования проектов на диаграммах Ганта: направления и проекты, несколько
планов у проекта, задачи, вехи, группы, связи, пометки, исполнители, комментарии, история изменений и
сохранённые версии, совместная работа нескольких сотрудников над одним планом, роли и доступы, вход через
домен (LDAP), реестр изменений.

Версия приложения видна внизу бокового меню и в `/api/health/ready`. Источник версии — поле `version` в
корневом `package.json`; история для пользователей — [`docs/versions.md`](docs/versions.md), для
администраторов — [`docs/versions_admin.md`](docs/versions_admin.md).

## Возможности

- проекты по направлениям с прогрессом, просрочками и ближайшей вехой на главной странице;
- несколько планов (версий) у проекта: копия, импорт JSON, архив вместо удаления, корзина;
- диаграмма Ганта внутри приложения: задачи, вехи, группы любой вложенности, связи FS/SS/FF/SF с лагом и
  автосдвигом, временной сдвиг, прогресс, категории и легенда, пометки на шкале, ссылки Jira;
- шкала дни / недели / месяцы / кварталы, производственный календарь РФ 2025–2027;
- карточка строки, комментарии с ответами, история строки, фильтры, поиск, клавиатура, отмена и повтор;
- совместная работа: правки по полям без потерь, изменения коллег через несколько секунд, «сейчас на плане»;
- история плана: журнал «кто, когда, было → стало», автоматические и ручные версии с восстановлением;
- экспорт PNG, PDF, SVG, CSV для Excel, JSON (совместим с 1.0);
- роли «Администратор», «Менеджер», «Редактор», «Наблюдатель»; доступы к проектам и направлениям;
- вход по паролю системы и через LDAP / Active Directory;
- реестр изменений с фильтрами и выгрузкой CSV;
- PostgreSQL в production, SQLite для разработки и тестов; Docker Compose, миграции Alembic, ежедневные
  резервные копии, перенос данных 1.0.x со сверкой.

## Документация

| Документ | Форматы |
| --- | --- |
| Руководство пользователя | [MD](docs/manuals/user-guide.md), [DOCX](docs/manuals/user-guide.docx), [PDF](docs/manuals/user-guide.pdf) |
| Руководство администратора | [MD](docs/manuals/admin-guide.md), [DOCX](docs/manuals/admin-guide.docx), [PDF](docs/manuals/admin-guide.pdf) |
| Руководство по развёртыванию | [MD](docs/manuals/deployment-guide.md), [DOCX](docs/manuals/deployment-guide.docx), [PDF](docs/manuals/deployment-guide.pdf) |
| Переход с 1.0.1 (runbook) | [MD](docs/runbooks/v0.2.0-migration-from-1.0.1.md) |
| Ревизия 1.0.1: найденные дефекты и решения | [MD](docs/review/v0.2.0-revision-1.0.1.md) |
| Нагрузочное тестирование | [MD](docs/performance/loadtest-v0.2.0.md) |
| Архитектурные решения | [ADR-0001](docs/architecture/adr-0001-platform-and-stack.md) — [ADR-0006](docs/architecture/adr-0006-native-gantt-editor.md) |
| Release notes | [v0.2.0](docs/release-notes/v0.2.0.md) |

DOCX и PDF собираются из Markdown командой `python3 tools/docs/build_manuals.py` (pandoc, python-docx, pypdf,
Chromium из Playwright). Снимки экранов — `node tools/docs/screenshots.mjs` на демонстрационных данных
вымышленной компании (`tools/demo/seed_demo.py`).

## Что нового в 0.2.0

- новая платформа на стеке todo-planner: Flask + PostgreSQL + Alembic, Vue 3, Docker Compose ([ADR-0001](docs/architecture/adr-0001-platform-and-stack.md), [release notes](docs/release-notes/v0.2.0.md));
- диаграмма Ганта — часть приложения, а не встроенная страница; окна приложения вместо окон браузера ([ADR-0006](docs/architecture/adr-0006-native-gantt-editor.md));
- одновременная работа над планом без потерь: операции по полям, конфликты, изменения коллег на экране, история и версии ([ADR-0002](docs/architecture/adr-0002-data-model-and-concurrency.md));
- сессии, CSRF, ограничение входа, заголовки безопасности; исправлены уязвимости 1.0.1 ([ADR-0003](docs/architecture/adr-0003-sessions-and-security.md), [ревизия](docs/review/v0.2.0-revision-1.0.1.md));
- роли и доступы с реальными ограничениями, доступ к направлению целиком ([ADR-0004](docs/architecture/adr-0004-roles-and-access.md));
- перенос базы 1.0.1 без потерь со сверкой каждого плана ([ADR-0005](docs/architecture/adr-0005-legacy-migration.md), [runbook](docs/runbooks/v0.2.0-migration-from-1.0.1.md)).

## Стек

### Frontend

- Vue 3, Pinia, Vue Router, Vite;
- собственная дизайн-система (токены todo-planner), SVG-диаграмма, jsPDF;
- Vitest, ESLint 9 и `eslint-plugin-vue`; e2e — Playwright.

### Backend

- Python 3.12, Flask 3, gunicorn (процессы sync);
- Flask-SQLAlchemy и SQLAlchemy 2, Alembic;
- Flask-WTF (CSRF), Flask-Limiter, pydantic 2, bcrypt, ldap3;
- PostgreSQL 16 в production, SQLite в разработке и тестах;
- pytest и Ruff.

### Runtime

- Docker и Docker Compose V2;
- nginx: SPA, проксирование `/api`, сжатие, заголовки безопасности;
- PostgreSQL 16 Alpine;
- ежедневный `pg_dump -Fc` в контейнере `backup`;
- HTTPS — внешним прокси (`Caddyfile.example` или nginx сервера).

## Быстрый запуск через Docker

### Требования

- Docker Engine с Compose V2 (Ubuntu: `sudo apt install docker.io docker-compose-v2`);
- свободный порт 39663 (меняется в `.env`);
- 2 ГБ ОЗУ, 5 ГБ диска.

### Новая установка

```bash
cp .env.example .env
openssl rand -hex 32   # → SECRET_KEY
openssl rand -hex 24   # → POSTGRES_PASSWORD
# BOOTSTRAP_ADMIN_PASSWORD — пароль первого администратора (логин admin), только для пустой базы
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:39663/api/health/ready
```

После запуска: приложение — `http://<сервер>:39663`, журнал — `docker compose logs -f backend`. Backend
применяет миграции перед стартом. Не удаляйте том базы (`docker compose down -v`) при обычной работе.

### Переход с 1.0.1

Данные 1.0.1 переносятся одной командой со сверкой; порядок, репетиция и откат —
[runbook](docs/runbooks/v0.2.0-migration-from-1.0.1.md):

```bash
cp /puls_proekta/puls.db* import/                       # рабочая база 1.0.1 с сервера (служба остановлена)
docker compose --profile migrate run --rm legacy-import  # перенос, сверка, отчёт import/import-report.md
docker compose up -d
```

## Что находится в контейнерах

- `db` — PostgreSQL 16 Alpine, данные в томе `db_data`;
- `backend` — Python 3.12 slim, Flask под gunicorn, миграции при старте, непривилегированный пользователь;
- `frontend` — сборка Vite и nginx: отдаёт SPA, проксирует `/api`, единственный открытый порт;
- `backup` — клиент PostgreSQL: ежедневная копия, хранение, восстановление (`deploy/backup.sh`);
- `legacy-import` — только профиль `migrate`: перенос базы 1.0.x.

## Переменные окружения

Шаблон — [`.env.example`](.env.example).

| Переменная | Назначение |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_DB` | Пользователь и база PostgreSQL (по умолчанию `puls`) |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL — длинный случайный |
| `SECRET_KEY` | Обязательный секрет подписи (≥ 32 символов): `openssl rand -hex 32` |
| `HTTP_PORT`, `HTTP_BIND` | Порт и адрес, на которых открывается система (`39663`, `0.0.0.0`) |
| `SESSION_COOKIE_SECURE` | `true` — когда система открывается только по HTTPS |
| `SESSION_LIFETIME_DAYS` | Срок жизни сессии без входа, дней (30) |
| `LOGIN_RATE_LIMIT` | Попыток входа на одну учётную запись (`10 per minute;50 per hour`) |
| `LOGIN_IP_RATE_LIMIT` | Попыток входа с одного адреса (`300 per minute`, офис за NAT) |
| `TRUSTED_PROXY_HOPS` | Прокси перед backend: `1` — nginx контейнера, `2` — плюс HTTPS-прокси сервера |
| `SNAPSHOT_INTERVAL_MINUTES` | Как часто сохранять автоснимок плана при правках, минут (30) |
| `BOOTSTRAP_ADMIN_PASSWORD` | Только новая установка без переноса: пароль первого администратора `admin` |
| `BACKUP_DIR`, `BACKUP_HOUR`, `BACKUP_RETENTION_DAYS` | Каталог копий на сервере, час ежедневной копии, срок хранения |
| `TZ` | Часовой пояс (`Europe/Moscow`) |
| `GUNICORN_WORKERS` | Процессов backend; пусто — 2 × ядра + 1 (не больше 9) |

Не коммитьте `.env`, базы и резервные копии — `.gitignore` их исключает.

## Разработка

```bash
# backend: http://127.0.0.1:5000
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
FLASK_ENV=development python scripts/migrate_on_start.py
python ../tools/demo/seed_demo.py          # демо-данные ООО «Вектор», пароль demo-password-1 (только пустая база)
FLASK_ENV=development flask --app wsgi run

# frontend: http://localhost:5173, /api проксируется на backend
npm ci
npm run dev
```

## Production и HTTPS

Рекомендуемая схема: `сотрудники → HTTPS-прокси :443 → frontend nginx :39663 → backend :5000 → PostgreSQL`.
Для Caddy — [`Caddyfile.example`](Caddyfile.example); для nginx сервера — пример в руководстве по
развёртыванию. После включения HTTPS: `HTTP_BIND=127.0.0.1`, `SESSION_COOKIE_SECURE=true`, `TRUSTED_PROXY_HOPS=2`.

## База данных и миграции

- production — PostgreSQL (`db`), разработка — SQLite `backend/puls_dev.db`, тесты — SQLite в памяти или
  `TEST_DATABASE_URL`;
- миграции Alembic в `backend/alembic/versions`, применяются при старте backend (`AUTO_MIGRATE=true`);
- вручную: `docker compose exec backend alembic current`, `alembic history`.

## Резервное копирование

Контейнер `backup` каждый день в `BACKUP_HOUR` делает `pg_dump -Fc` в `BACKUP_DIR` (права 600) и удаляет копии
старше `BACKUP_RETENTION_DAYS`. `deploy/update.sh` делает копию перед каждым обновлением.

```bash
docker compose exec backup sh /usr/local/bin/backup.sh now manual   # копия сейчас
docker compose exec backup sh /usr/local/bin/backup.sh list
docker compose stop backend
docker compose exec backup sh /usr/local/bin/backup.sh restore puls_20261030-030000_daily.dump
docker compose start backend
```

Восстановление сначала сохраняет текущее состояние (`pre-restore`) и отказывается работать, пока к базе
подключён backend. Для настоящей защиты копируйте `BACKUP_DIR` на другой сервер.

## Логирование и безопасность

- журналы backend и nginx — в stdout контейнеров (`docker compose logs`);
- серверные сессии с хэшированными токенами, `HttpOnly`, `SameSite=Lax`, `Secure` по настройке;
- CSRF для всех изменяющих запросов, ограничение попыток входа, bcrypt;
- права проверяются на сервере, интерфейс лишь прячет недоступное;
- nginx: `nosniff`, `X-Frame-Options`, `Referrer-Policy`, Content-Security-Policy.

## Обновление

```bash
cd /opt/puls
sudo sh deploy/update.sh v0.2.1   # копия pre-update → git → сборка → миграции → проверка готовности
```

При ошибке скрипт печатает команды отката (прежняя версия и восстановление копии `pre-update`). Порядок и
проверки — руководство по развёртыванию.

## Сборка и тестирование

```bash
# frontend
npm ci && npm run lint && npm test && npm run build

# backend
cd backend && pip install -r requirements-dev.txt
ruff check . && pytest -q
TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/puls_test pytest -q

# e2e: SQLite со снимком 1.0.1, Flask :5055, vite preview :4173, Playwright (Chromium)
npm run build && npm run e2e

# нагрузка — только на тестовом стенде
python3 tools/perf/loadtest.py http://127.0.0.1:39663 --login admin --password '…'
```

## CI

`.github/workflows/ci.yml` на каждый коммит в `main` и `v*` и на pull request:

- клиент: eslint, vitest, сборка;
- сервер: ruff, pytest (SQLite), одна голова Alembic, `alembic check`, downgrade/upgrade;
- сервер на PostgreSQL: pytest, миграции, перенос снимка 1.0.1 со сверкой (отчёт — в сводке задания);
- e2e: Playwright на перенесённых данных, снимки экранов — артефакт;
- Docker: сборка образов, перенос снимка через профиль `migrate`, запуск стека, проверка готовности и CSP,
  резервная копия.

## Структура репозитория

```text
.
├── src/                    # клиент Vue: views, stores, components, gantt (domain, editor, chart, export)
├── backend/                # Flask: app (api, services, models), alembic, scripts, tests
├── e2e/                    # e2e на Playwright (node:test)
├── deploy/                 # backup.sh, update.sh
├── import/                 # сюда кладётся puls.db 1.0.x для переноса (в git не попадает)
├── legacy/                 # снимок базы 1.0.1 для тестов переноса, страница Ганта 1.0.1 для сверки
├── tools/                  # demo (демо-данные), docs (снимки экранов, сборка руководств), perf (нагрузка)
├── docs/                   # руководства, ADR, runbooks, release notes, ревизия, нагрузка
├── Dockerfile              # сборка клиента + nginx
├── docker-compose.yml      # db, backend, frontend, backup, legacy-import
├── nginx.conf              # SPA, прокси /api, заголовки безопасности
├── Caddyfile.example       # HTTPS
└── .env.example            # шаблон конфигурации
```

## API и аутентификация

Префикс `/api`. Группы: `/auth`, `/profile`, `/users`, `/directions`, `/projects`, `/plans`, `/comments`,
`/access`, `/audit`, `/settings`, `/calendar`, `/trash`, `/health`, `/meta`.

План: `GET /api/plans/<id>` — план целиком; `POST /api/plans/<id>/ops` — пакет операций; `GET
/api/plans/<id>/changes?since=<ревизия>` — изменения коллег и присутствие; `/events`, `/snapshots`,
`/items/<key>/comments`, `/export?format=legacy|snapshot`.

Аутентификация cookie-based:

1. `GET /api/auth/csrf` — CSRF-токен;
2. `POST /api/auth/login` с заголовком `X-CSRF-Token`;
3. `GET /api/auth/me` — текущий сотрудник;
4. все POST/PATCH/PUT/DELETE — с `X-CSRF-Token`;
5. `POST /api/auth/logout`.

## Ограничения

- изменения коллег приходят опросом (4 с), не WebSocket;
- ограничение попыток входа считается в памяти каждого процесса backend; для строгого общего предела нужен Redis;
- резервные копии лежат на том же сервере — копируйте `BACKUP_DIR` на другой сервер;
- мобильная раскладка не оптимизирована: диаграмма рассчитана на экраны от 1280 px;
- интеграция с todo-planner — в планах: общий стек и подходы заложены (ADR-0001).

## Авторство

© Alexander Yurasov. Внутренняя система компании; перед внешней публикацией определите лицензию и правила
обработки персональных данных.
