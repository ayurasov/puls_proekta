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

dump() {
  label="${1:-manual}"
  file="$DIR/puls_$(date +%Y%m%d-%H%M%S)_${label}.dump"
  if pg_dump -Fc -f "$file.tmp"; then
    mv "$file.tmp" "$file"
    echo "[backup] $(date '+%F %T') готово: $file ($(du -h "$file" | cut -f1))"
  else
    rm -f "$file.tmp"
    echo "[backup] $(date '+%F %T') ОШИБКА резервного копирования" >&2
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
    echo "[backup] ежедневная копия в ${BACKUP_HOUR:-03}:00, хранение ${BACKUP_RETENTION_DAYS:-30} дней"
    last=""
    while true; do
      today=$(date +%F)
      if [ "$(date +%H)" = "${BACKUP_HOUR:-03}" ] && [ "$last" != "$today" ]; then
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
