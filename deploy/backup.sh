#!/bin/sh
# Резервные копии PostgreSQL (контейнер backup).
#   backup.sh loop          — ежедневно в BACKUP_HOUR:00 (по TZ), хранить BACKUP_RETENTION_DAYS дней
#   backup.sh now [метка]   — копия сейчас (например, перед обновлением: now pre-update)
#   backup.sh list          — список копий
#   backup.sh restore ФАЙЛ  — восстановить базу из копии (сначала остановите backend,
#                             перед восстановлением автоматически делается копия pre-restore)
set -eu
# В копиях — хэши паролей и вся переписка: доступ только владельцу
umask 077
DIR=/backups
mkdir -p "$DIR"

# Итог копии — в базу (app_settings.backup_status): его показывает «Состояние системы» (ADR-0014). Не удалось
# записать — копия всё равно сделана, об этом только сообщение в журнале.
report() {
  at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  count=$(find "$DIR" -name 'puls_*.dump' | wc -l | tr -d ' ')
  total=$(find "$DIR" -name 'puls_*.dump' -exec stat -c %s {} + 2>/dev/null | awk '{s+=$1} END {print s+0}')
  if [ "$1" = "ok" ]; then
    patch="{\"ok\":true,\"at\":\"$at\",\"lastOkAt\":\"$at\",\"file\":\"$(basename "$2")\",\"size\":$(stat -c %s "$2"),\"label\":\"$3\",\"count\":$count,\"totalBytes\":$total}"
  else
    patch="{\"ok\":false,\"at\":\"$at\",\"label\":\"$3\",\"error\":\"ошибка pg_dump — подробности в журнале контейнера backup\",\"count\":$count,\"totalBytes\":$total}"
  fi
  psql -q -v ON_ERROR_STOP=1 -c "insert into app_settings (key, value, updated_at) values ('backup_status', '$patch'::json, now())
    on conflict (key) do update set value = (coalesce(app_settings.value::jsonb, '{}'::jsonb) || '$patch'::jsonb)::json,
    updated_at = now()" >/dev/null 2>&1 \
    || echo "[backup] не удалось записать итог копии в базу (раздел «Состояние системы»)" >&2
}

dump() {
  label="${1:-manual}"
  file="$DIR/puls_$(date +%Y%m%d-%H%M%S)_${label}.dump"
  if pg_dump -Fc -f "$file.tmp"; then
    mv "$file.tmp" "$file"
    echo "[backup] $(date '+%F %T') готово: $file ($(du -h "$file" | cut -f1))"
    report ok "$file" "$label"
  else
    rm -f "$file.tmp"
    echo "[backup] $(date '+%F %T') ОШИБКА резервного копирования" >&2
    report error "" "$label"
    return 1
  fi
}

case "${1:-loop}" in
  now)
    dump "${2:-manual}"
    ;;
  list)
    ls -lh "$DIR"
    ;;
  restore)
    src="${2:?укажите файл копии: backup.sh restore /backups/puls_….dump}"
    [ -f "$src" ] || src="$DIR/$src"
    [ -f "$src" ] || { echo "[backup] файл не найден: ${2}" >&2; exit 1; }
    pg_restore --list "$src" >/dev/null || { echo "[backup] файл не является копией pg_dump: $src" >&2; exit 1; }
    active=$(psql -Atc "select count(*) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()")
    if [ "$active" != "0" ]; then
      echo "[backup] к базе подключено сеансов: $active. Остановите приложение: docker compose stop backend" >&2
      exit 1
    fi
    dump pre-restore
    pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction -d "$PGDATABASE" "$src"
    echo "[backup] $(date '+%F %T') база восстановлена из $src. Запустите приложение: docker compose start backend"
    ;;
  loop)
    # BACKUP_HOUR=3 и BACKUP_HOUR=03 — одно и то же; date +%H всегда с ведущим нулём
    hour=$(printf '%02d' "$(echo "${BACKUP_HOUR:-03}" | sed 's/^0*//; s/^$/0/')")
    echo "[backup] ежедневная копия в ${hour}:00, хранение ${BACKUP_RETENTION_DAYS:-30} дней"
    last=""
    while true; do
      today=$(date +%F)
      if [ "$(date +%H)" = "$hour" ] && [ "$last" != "$today" ]; then
        dump daily && last="$today"
        find "$DIR" -name 'puls_*_daily.dump' -mtime +"${BACKUP_RETENTION_DAYS:-30}" -delete
      fi
      sleep 60
    done
    ;;
  *)
    echo "usage: backup.sh loop|now [label]|list|restore FILE" >&2
    exit 2
    ;;
esac
