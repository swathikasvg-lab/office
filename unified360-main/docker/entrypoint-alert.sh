#!/bin/sh
set -eu

echo "[alert] waiting for postgres at ${DB_HOST:-postgres}:${DB_PORT:-5432}"
python - <<'PY'
import os
import sys
import time
import psycopg2

host = os.getenv("DB_HOST", "postgres")
port = int(os.getenv("DB_PORT", "5432"))
user = os.getenv("DB_USER", "autointelli")
password = os.getenv("DB_PASSWORD", "")
dbname = os.getenv("DB_NAME", "opsduty")
timeout = int(os.getenv("DB_WAIT_TIMEOUT", "60"))

start = time.time()
while True:
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
        )
        conn.close()
        print("[alert] postgres is ready")
        break
    except Exception as exc:
        if time.time() - start >= timeout:
            print(f"[alert] database timeout after {timeout}s: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PY

echo "[alert] starting: $*"
exec "$@"
