#!/bin/bash
set -euo pipefail

echo "[postgres-init] ensuring Grafana database exists"

if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -tAc "SELECT 1 FROM pg_database WHERE datname='autointelli'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -c "CREATE DATABASE autointelli OWNER ${POSTGRES_USER};"
fi

echo "[postgres-init] importing /docker-entrypoint-initdb.d/grafana.sql into autointelli"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname autointelli -f /docker-entrypoint-initdb.d/grafana.sql

echo "[postgres-init] done"
