#!/bin/sh
# Резервные копии PostgreSQL (контейнер backup, ADR-0015).
#   backup.sh loop          — служба: раз в минуту сверяет расписание и запросы из «Пульса»
#   backup.sh tick          — один шаг службы (для проверки)
#   backup.sh now [метка]   — копия сейчас (например, перед обновлением: now pre-update)
#   backup.sh list          — список копий
#   backup.sh restore ФАЙЛ  — восстановить базу из копии (сначала остановите backend,
#                             перед восстановлением автоматически делается копия pre-restore)
#
# Расписание и хранение администратор задаёт в «Пульсе» (Администрирование → Резервные копии):
# app_settings.backup_settings = {enabled, time "ЧЧ:ММ", days [1..7, 1 — понедельник], retentionDays, keepManualDays}.
# Пока настроек в базе нет — BACKUP_HOUR (ежедневно в ЧЧ:00) и BACKUP_RETENTION_DAYS из окружения, как в 0.7.2.
# «Сделать копию сейчас» в «Пульсе» — запись app_settings.backup_request: служба выполняет её в течение минуты.
# Итоги, список копий и отметка «служба работает» — в app_settings.backup_status (раздел «Состояние системы»).
set -eu
# В копиях — хэши паролей и вся переписка: доступ только владельцу
umask 077
DIR=${BACKUP_PATH:-/backups}
mkdir -p "$DIR"

log() { echo "[backup] $(date '+%F %T') $*"; }
q() { psql -XAtq -v ON_ERROR_STOP=1 "$@" 2>/dev/null; }

# Слить JSON-объект в app_settings.backup_status. Не удалось — копии делаются всё равно, это только сообщение.
status_patch() {
  q -c "insert into app_settings (key, value, updated_at) values ('backup_status', '$1'::json, now())
    on conflict (key) do update set value = (coalesce(app_settings.value::jsonb, '{}'::jsonb) || '$1'::jsonb)::json,
    updated_at = now()" >/dev/null \
    || echo "[backup] не удалось записать состояние в базу (раздел «Состояние системы»)" >&2
}

utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Список копий (новые сверху, до 60) и итог: «"files":[…],"count":N,"totalBytes":N»
files_json() {
  list=$(ls -1t "$DIR" 2>/dev/null | grep -E '^puls_[0-9]{8}-[0-9]{6}_[A-Za-z0-9_-]+\.dump$' || true)
  count=0; total=0; items=""
  for f in $list; do
    size=$(stat -c %s "$DIR/$f" 2>/dev/null || echo 0)
    count=$((count + 1)); total=$((total + size))
    if [ "$count" -le 60 ]; then
      at=$(date -u -d "@$(stat -c %Y "$DIR/$f")" +%Y-%m-%dT%H:%M:%SZ)
      items="$items{\"name\":\"$f\",\"size\":$size,\"at\":\"$at\"},"
    fi
  done
  printf '"files":[%s],"count":%s,"totalBytes":%s' "${items%,}" "$count" "$total"
}

# Настройки: из базы, если администратор их задал; иначе — из окружения. База недоступна — прежние.
ENV_TIME="$(printf '%02d' "$(echo "${BACKUP_HOUR:-03}" | sed 's/^0*//; s/^$/0/')"):00"
EN=1; TIME=$ENV_TIME; DAYS=1234567; RET=${BACKUP_RETENTION_DAYS:-30}; KEEP=0; SRC=env
read_settings() {
  if ! row=$(q -F '|' -c "select coalesce((value::jsonb->>'enabled')::boolean, true)::int,
        coalesce(value::jsonb->>'time', ''),
        coalesce((select string_agg(d, '' order by d) from jsonb_array_elements_text(
          case when jsonb_typeof(value::jsonb->'days') = 'array' then value::jsonb->'days' else '[]'::jsonb end) d), ''),
        coalesce(value::jsonb->>'retentionDays', ''), coalesce(value::jsonb->>'keepManualDays', '')
        from app_settings where key = 'backup_settings'"); then
    return 0
  fi
  EN=1; TIME=$ENV_TIME; DAYS=1234567; RET=${BACKUP_RETENTION_DAYS:-30}; KEEP=0; SRC=env
  [ -n "$row" ] || return 0
  IFS='|' read -r en time days ret keep <<EOF
$row
EOF
  case "$time" in [0-2][0-9]:[0-5][0-9]) ;; *) time="" ;; esac
  case "$days" in *[!1-7]*) days="" ;; esac
  case "$ret" in ''|*[!0-9]*) ret="" ;; esac
  case "$keep" in ''|*[!0-9]*) keep="" ;; esac
  EN=$en; TIME=${time:-$TIME}; DAYS=$days; RET=${ret:-$RET}; KEEP=${keep:-0}; SRC=settings
}

service_json() {
  days=$(echo "$DAYS" | sed 's/./&,/g; s/,$//')
  enabled=$([ "$EN" = 1 ] && echo true || echo false)
  printf '"service":{"enabled":%s,"time":"%s","days":[%s],"retentionDays":%s,"keepManualDays":%s,"source":"%s","tz":"%s","utcOffset":"%s"}' \
    "$enabled" "$TIME" "$days" "$RET" "$KEEP" "$SRC" "$(echo "${TZ:-UTC}" | tr -cd 'A-Za-z0-9/_+-')" "$(date +%z)"
}

heartbeat() {
  status_patch "{\"seenAt\":\"$(utc)\",$(service_json),$(files_json)}"
}

# Хранение: ежедневные — retentionDays дней, остальные (вручную, перед обновлением) — keepManualDays (0 — всегда).
# -exec rm, а не -delete: -delete есть не в каждой сборке busybox
cleanup() {
  find "$DIR" -name 'puls_*_daily.dump' -mtime +"$RET" -exec rm -f {} + 2>/dev/null || true
  if [ "$KEEP" -gt 0 ]; then
    find "$DIR" -name 'puls_*.dump' ! -name 'puls_*_daily.dump' -mtime +"$KEEP" -exec rm -f {} + 2>/dev/null || true
  fi
}

dump() {
  label=$(echo "${1:-manual}" | tr -cd 'A-Za-z0-9_-')
  label=${label:-manual}
  file="$DIR/puls_$(date +%Y%m%d-%H%M%S)_${label}.dump"
  daily=""
  [ "$label" = "daily" ] && daily=",\"lastDailyDate\":\"$(date +%F)\""
  status_patch "{\"running\":{\"label\":\"$label\",\"startedAt\":\"$(utc)\"}}"
  if pg_dump -Fc -f "$file.tmp"; then
    mv "$file.tmp" "$file"
    log "готово: $file ($(du -h "$file" | cut -f1))"
    at=$(utc)
    cleanup
    status_patch "{\"ok\":true,\"at\":\"$at\",\"lastOkAt\":\"$at\",\"file\":\"$(basename "$file")\",\"size\":$(stat -c %s "$file"),\"label\":\"$label\",\"error\":null,\"running\":null$daily,$(files_json)}"
  else
    rm -f "$file.tmp"
    echo "[backup] $(date '+%F %T') ОШИБКА резервного копирования" >&2
    status_patch "{\"ok\":false,\"at\":\"$(utc)\",\"label\":\"$label\",\"error\":\"ошибка pg_dump — подробности в журнале контейнера backup\",\"running\":null$daily,$(files_json)}"
    return 1
  fi
}

# Один шаг службы: запрос «Сделать копию сейчас», расписание, отметка «служба работает» (раз в 5 минут)
LAST_DAILY=""
tick() {
  read_settings
  acted=""
  req=$(q -c "select value::jsonb->>'requestedAt' from app_settings where key = 'backup_request'" || true)
  if [ -n "$req" ]; then
    # Запрос снимается до копии: повторный шаг во время долгой копии не начнёт вторую
    q -c "delete from app_settings where key = 'backup_request'" >/dev/null || true
    log "копия по запросу из «Пульса»"
    dump manual || true
    acted=1
  fi
  today=$(date +%F)
  done_today=$(q -c "select value::jsonb->>'lastDailyDate' from app_settings where key = 'backup_status'" || echo "$LAST_DAILY")
  hm=$(echo "$TIME" | tr -d ':')
  if [ "$EN" = 1 ] && [ -n "$hm" ] && [ "$(date +%H%M)" -ge "$hm" ] && [ "${done_today:-$LAST_DAILY}" != "$today" ]; then
    case "$DAYS" in
      *"$(date +%u)"*)
        LAST_DAILY=$today
        dump daily || true
        acted=1
        ;;
    esac
  fi
  if [ -n "$acted" ] || [ "$(( $(date +%s) / 60 % 5 ))" = 0 ] || [ "${1:-}" = "first" ]; then
    heartbeat
  fi
}

case "${1:-loop}" in
  now)
    read_settings
    dump "${2:-manual}"
    ;;
  tick)
    tick first
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
    read_settings
    dump pre-restore
    pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction -d "$PGDATABASE" "$src"
    echo "[backup] $(date '+%F %T') база восстановлена из $src. Запустите приложение: docker compose start backend"
    ;;
  loop)
    read_settings
    if [ "$SRC" = settings ]; then
      log "служба запущена: расписание и хранение — из «Пульса» (Администрирование → Резервные копии)"
    else
      log "служба запущена: ежедневная копия в $TIME, хранение $RET дней (BACKUP_HOUR, BACKUP_RETENTION_DAYS)"
    fi
    first=first
    while true; do
      tick $first || log "шаг службы не выполнен — повтор через минуту"
      first=""
      sleep 60
    done
    ;;
  *)
    echo "usage: backup.sh loop|tick|now [label]|list|restore FILE" >&2
    exit 2
    ;;
esac
