#!/bin/sh
set -eu

case "${RUN_MIGRATIONS_ON_START:-true}" in
  0|false|FALSE|no|NO|off|OFF)
    echo "Skipping startup migrations because RUN_MIGRATIONS_ON_START is disabled."
    ;;
  *)
    echo "Preparing the SulitShelf database..."
    python -m flask --app run.py deploy-release
    ;;
esac

echo "Starting SulitShelf with Gunicorn..."
exec gunicorn -c gunicorn.conf.py run:app
