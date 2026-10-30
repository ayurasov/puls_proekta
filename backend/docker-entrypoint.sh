#!/bin/sh
set -eu

case "${AUTO_MIGRATE:-true}" in
  true|TRUE|1|yes|YES)
    echo "Applying database migrations before application startup..."
    python scripts/migrate_on_start.py
    ;;
  false|FALSE|0|no|NO)
    echo "Automatic database migrations are disabled by AUTO_MIGRATE=${AUTO_MIGRATE}."
    ;;
  *)
    echo "AUTO_MIGRATE must be true or false."
    exit 1
    ;;
esac

exec "$@"
