#!/bin/sh
# Обновление «Пульса Проекта» на сервере (Docker Compose):
#   1) резервная копия базы с меткой pre-update;
#   2) получение версии из git (ветка или тег, по умолчанию — текущая ветка);
#   3) пересборка и перезапуск; миграции применяются при старте backend;
#   4) проверка готовности.
# Запуск из каталога установки: sh deploy/update.sh [ветка|тег]
set -eu
REF="${1:-}"
PREV=$(git rev-parse --short HEAD)

rollback_hint() {
  echo "Откат кода: git checkout $PREV && docker compose up -d --build" >&2
  echo "Если миграции успели изменить базу — восстановление из копии pre-update:" >&2
  echo "  docker compose stop backend && docker compose exec backup sh /usr/local/bin/backup.sh restore <файл pre-update>" >&2
}

echo "== Резервная копия перед обновлением"
docker compose exec -T backup sh /usr/local/bin/backup.sh now pre-update

echo "== Получение версии ${REF:-(текущая ветка)}"
git fetch --all --tags
if [ -n "$REF" ]; then git checkout "$REF"; fi
# Тег — это отсоединённый HEAD, подтягивать нечего; ветку — только перемоткой вперёд
if git symbolic-ref -q HEAD >/dev/null; then git pull --ff-only; fi
echo "Версия: $PREV → $(git rev-parse --short HEAD)"

echo "== Сборка и перезапуск"
docker compose up -d --build || { rollback_hint; exit 1; }

echo "== Проверка готовности"
PORT=$(grep -E '^HTTP_PORT=' .env 2>/dev/null | cut -d= -f2)
PORT=${PORT:-39663}
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health/ready" >/dev/null 2>&1; then
    curl -fsS "http://127.0.0.1:${PORT}/api/health/ready"; echo
    echo "== Готово"
    exit 0
  fi
  sleep 5
done
echo "Система не ответила за 5 минут. Журнал: docker compose logs backend --tail 100" >&2
rollback_hint
exit 1
