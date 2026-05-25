#!/bin/sh
set -e

if [ -n "${DATABASE_URL}" ] && echo "${DATABASE_URL}" | grep -q '^postgresql'; then
  export DATABASE_USE_ALEMBIC_ONLY="${DATABASE_USE_ALEMBIC_ONLY:-true}"
fi

exec "$@"
