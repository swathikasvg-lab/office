 #!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}"

MODE="native"
WITH_GRAFANA_STACK=1
WITH_CERTBOT=0
WITH_METRICS=1
SKIP_PACKAGES=0
SKIP_BOOTSTRAP=0
SKIP_GRAFANA_IMPORT=0

DOMAIN="performance.speedcloud.co.in"
CERTBOT_EMAIL=""

SERVICE_USER="unified360"
INSTALL_ROOT="/opt/unified360"
APP_DIR="${INSTALL_ROOT}/app"
DATA_DIR="${INSTALL_ROOT}/data"
ENV_FILE="/etc/unified360/unified360.env"

DB_USER="autointelli"
DB_PASSWORD="CHANGE_ME_STRONG_PASSWORD"
OPS_DB_NAME="opsduty"
GRAFANA_DB_NAME="autointelli"

ADMIN_PASSWORD="ChangeMeAdmin@123"
FLASK_SECRET_KEY=""
NMS_SECRET_KEY=""
GRAFANA_BASE_URL=""

PKG_MGR=""

ROOT_CMD=()

log() {
  echo "[install] $*"
}

warn() {
  echo "[install][warn] $*" >&2
}

die() {
  echo "[install][error] $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./install.sh [options]

Modes:
  --mode native|docker              Install on host (default: native) or run docker compose

Core options:
  --domain <fqdn>                   Domain for Grafana/Nginx config
  --db-user <name>                  PostgreSQL user (default: autointelli)
  --db-password <password>          PostgreSQL password
  --ops-db <name>                   Unified360 DB name (default: opsduty)
  --grafana-db <name>               Grafana DB name (default: autointelli)
  --admin-password <password>       Bootstrap admin password
  --flask-secret <secret>           FLASK_SECRET_KEY (auto-generated if omitted)
  --nms-secret <secret>             NMS_SECRET_KEY (auto-generated if omitted)
  --grafana-base-url <url>          GRAFANA_BASE_URL (default from domain)

Native mode options:
  --no-grafana-stack                Skip Grafana + Nginx setup
  --no-metrics                      Skip InfluxDB + Prometheus installation
  --with-certbot                    Run certbot for domain (requires --certbot-email)
  --certbot-email <email>           Email used by certbot
  --skip-packages                   Skip apt/dnf package installation
  --skip-bootstrap                  Skip bootstrap.py execution
  --skip-grafana-import             Skip grafana.sql import

Docker mode options:
  --with-metrics                    Start compose with --profile metrics (default)
  --no-metrics                      Start compose without metrics profile

Examples:
  ./install.sh --db-password 'StrongPass' --admin-password 'Admin@123'
  ./install.sh --mode native --domain example.com --db-password 'StrongPass'
  ./install.sh --mode docker --db-password 'StrongPass'
  ./install.sh --mode docker --no-metrics --db-password 'StrongPass'
EOF
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

generate_secret() {
  if has_cmd openssl; then
    openssl rand -hex 32
  else
    python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
  fi
}

run_root() {
  if ((${#ROOT_CMD[@]})); then
    "${ROOT_CMD[@]}" "$@"
  else
    "$@"
  fi
}

run_as_user() {
  local user="$1"
  shift
  local cmd="$*"
  if ((${#ROOT_CMD[@]})); then
    sudo -u "$user" bash -lc "$cmd"
  else
    runuser -u "$user" -- bash -lc "$cmd"
  fi
}

run_as_postgres() {
  local cmd="$*"
  if ((${#ROOT_CMD[@]})); then
    sudo -u postgres bash -lc "$cmd"
  else
    runuser -u postgres -- bash -lc "$cmd"
  fi
}

wait_for_http() {
  local url="$1"
  local timeout="${2:-60}"
  local start
  start="$(date +%s)"

  while true; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    if (( "$(date +%s)" - start >= timeout )); then
      return 1
    fi
    sleep 2
  done
}

escape_sql_literal() {
  printf "%s" "$1" | sed "s/'/''/g"
}

escape_sql_ident() {
  printf "%s" "$1" | sed 's/"/""/g'
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode)
        MODE="${2:-}"
        shift 2
        ;;
      --domain)
        DOMAIN="${2:-}"
        shift 2
        ;;
      --db-user)
        DB_USER="${2:-}"
        shift 2
        ;;
      --db-password)
        DB_PASSWORD="${2:-}"
        shift 2
        ;;
      --ops-db)
        OPS_DB_NAME="${2:-}"
        shift 2
        ;;
      --grafana-db)
        GRAFANA_DB_NAME="${2:-}"
        shift 2
        ;;
      --admin-password)
        ADMIN_PASSWORD="${2:-}"
        shift 2
        ;;
      --flask-secret)
        FLASK_SECRET_KEY="${2:-}"
        shift 2
        ;;
      --nms-secret)
        NMS_SECRET_KEY="${2:-}"
        shift 2
        ;;
      --grafana-base-url)
        GRAFANA_BASE_URL="${2:-}"
        shift 2
        ;;
      --no-grafana-stack)
        WITH_GRAFANA_STACK=0
        shift
        ;;
      --no-metrics)
        WITH_METRICS=0
        shift
        ;;
      --with-certbot)
        WITH_CERTBOT=1
        shift
        ;;
      --certbot-email)
        CERTBOT_EMAIL="${2:-}"
        shift 2
        ;;
      --skip-packages)
        SKIP_PACKAGES=1
        shift
        ;;
      --skip-bootstrap)
        SKIP_BOOTSTRAP=1
        shift
        ;;
      --skip-grafana-import)
        SKIP_GRAFANA_IMPORT=1
        shift
        ;;
      --with-metrics)
        WITH_METRICS=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done
}

validate_inputs() {
  [[ -f "${SOURCE_DIR}/app.py" ]] || die "Run this script from repository root (app.py not found)."
  [[ -f "${SOURCE_DIR}/requirements.txt" ]] || die "requirements.txt not found."
  [[ "$MODE" == "native" || "$MODE" == "docker" ]] || die "--mode must be native or docker."
  [[ -n "$DB_PASSWORD" ]] || die "--db-password is required."
  [[ -n "$ADMIN_PASSWORD" ]] || die "--admin-password is required."

  if [[ -z "$FLASK_SECRET_KEY" ]]; then
    FLASK_SECRET_KEY="$(generate_secret)"
  fi
  if [[ -z "$NMS_SECRET_KEY" ]]; then
    NMS_SECRET_KEY="$(generate_secret)"
  fi
  if [[ -z "$GRAFANA_BASE_URL" ]]; then
    GRAFANA_BASE_URL="https://${DOMAIN}/grafana"
  fi

  if [[ "$WITH_CERTBOT" -eq 1 && -z "$CERTBOT_EMAIL" ]]; then
    die "--with-certbot requires --certbot-email"
  fi
}

prepare_privilege_model() {
  if [[ "${EUID}" -eq 0 ]]; then
    ROOT_CMD=()
  else
    has_cmd sudo || die "sudo is required when not running as root."
    ROOT_CMD=(sudo)
  fi
}

detect_pkg_mgr() {
  if has_cmd apt-get; then
    PKG_MGR="apt"
  elif has_cmd dnf; then
    PKG_MGR="dnf"
  else
    die "Unsupported OS: apt-get or dnf not found."
  fi
}

install_base_packages() {
  [[ "$SKIP_PACKAGES" -eq 1 ]] && {
    log "Skipping OS package install (--skip-packages)."
    return
  }

  detect_pkg_mgr
  log "Installing base packages using ${PKG_MGR}."

  if [[ "$PKG_MGR" == "apt" ]]; then
    run_root apt-get update -y
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      git curl rsync tar \
      python3 python3-venv python3-pip python3-dev \
      gcc libffi-dev libssl-dev libpq-dev \
      postgresql postgresql-contrib \
      nginx certbot python3-certbot-nginx
  else
    run_root dnf -y update
    run_root dnf install -y \
      git curl rsync tar \
      python3 python3-pip python3-devel \
      gcc libffi-devel openssl-devel postgresql-devel \
      postgresql-server postgresql \
      nginx certbot python3-certbot-nginx
  fi
}

install_grafana_pkg() {
  [[ "$WITH_GRAFANA_STACK" -eq 0 ]] && return
  [[ "$SKIP_PACKAGES" -eq 1 ]] && {
    log "Skipping Grafana package install (--skip-packages)."
    return
  }

  log "Installing Grafana package."

  if [[ "$PKG_MGR" == "apt" ]]; then
    run_root apt-get install -y apt-transport-https software-properties-common wget gnupg2
    run_root install -m 0755 -d /etc/apt/keyrings
    run_root bash -lc "wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg"
    run_root chmod a+r /etc/apt/keyrings/grafana.gpg
    run_root bash -lc "echo 'deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main' > /etc/apt/sources.list.d/grafana.list"
    run_root apt-get update -y
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y grafana
  else
    run_root tee /etc/yum.repos.d/grafana.repo >/dev/null <<'EOF'
[grafana]
name=grafana
baseurl=https://packages.grafana.com/oss/rpm
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://packages.grafana.com/gpg.key
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
EOF
    run_root dnf install -y grafana
  fi
}

install_influxdb_native() {
  [[ "$WITH_METRICS" -eq 0 ]] && {
    log "Skipping InfluxDB install (--no-metrics)."
    return
  }

  detect_pkg_mgr
  log "Installing InfluxDB 1.11.x."

  if [[ "$SKIP_PACKAGES" -eq 0 ]]; then
    if [[ "$PKG_MGR" == "apt" ]]; then
      run_root apt-get update -y
      run_root apt-get install -y curl gnupg2 ca-certificates
      run_root install -m 0755 -d /etc/apt/keyrings
      run_root bash -lc "curl -fsSL https://repos.influxdata.com/influxdata-archive_compat.key | gpg --dearmor > /etc/apt/keyrings/influxdata-archive_compat.gpg"
      run_root chmod a+r /etc/apt/keyrings/influxdata-archive_compat.gpg
      run_root bash -lc "echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main' > /etc/apt/sources.list.d/influxdata.list"
      run_root apt-get update -y

      local influx_ver
      influx_ver="$(apt-cache madison influxdb | awk '{print $3}' | grep -E '^1\.11\.' | sort -V | tail -n1 || true)"
      [[ -n "$influx_ver" ]] || die "Could not find InfluxDB 1.11.x in apt repository."
      run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "influxdb=${influx_ver}"
    else
      run_root tee /etc/yum.repos.d/influxdata.repo >/dev/null <<'EOF'
[influxdata]
name=InfluxData Repository - Stable
baseurl=https://repos.influxdata.com/rhel/$releasever/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://repos.influxdata.com/influxdata-archive_compat.key
EOF
      run_root dnf clean all
      run_root dnf makecache -y
      run_root dnf install -y "influxdb-1.11*"
    fi
  else
    log "Skipping InfluxDB package install (--skip-packages), will only enable service if present."
  fi

  if run_root bash -lc "systemctl list-unit-files | grep -q '^influxdb\\.service'"; then
    run_root systemctl enable --now influxdb
  else
    warn "influxdb.service not found. Install InfluxDB and rerun."
    return
  fi

  if ! wait_for_http "http://127.0.0.1:8086/ping" 60; then
    warn "InfluxDB did not become ready at http://127.0.0.1:8086."
  fi

  if has_cmd influx; then
    influx -host 127.0.0.1 -port 8086 -execute 'CREATE DATABASE "autointelli"' || true
    influx -host 127.0.0.1 -port 8086 -execute 'CREATE DATABASE "fortigate"' || true
    influx -host 127.0.0.1 -port 8086 -execute 'CREATE DATABASE "end_user_monitoring"' || true
  else
    warn "influx CLI not found; skipped automatic DB creation."
  fi
}

install_prometheus_native() {
  [[ "$WITH_METRICS" -eq 0 ]] && {
    log "Skipping Prometheus install (--no-metrics)."
    return
  }

  log "Installing latest Prometheus release."

  local arch
  case "$(uname -m)" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *)
      die "Unsupported architecture for Prometheus auto-install: $(uname -m)"
      ;;
  esac

  local release_info version asset_url
  release_info="$(python3 - "$arch" <<'PY'
import json
import sys
import urllib.request

arch = sys.argv[1]
url = "https://api.github.com/repos/prometheus/prometheus/releases/latest"
req = urllib.request.Request(url, headers={"User-Agent": "unified360-installer"})
with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.load(resp)

tag = data.get("tag_name", "").lstrip("v")
needle = f"linux-{arch}.tar.gz"
asset_url = ""
for asset in data.get("assets", []):
    name = asset.get("name", "")
    if name.endswith(needle):
        asset_url = asset.get("browser_download_url", "")
        break

if not tag or not asset_url:
    raise SystemExit("Could not resolve latest Prometheus linux asset URL.")

print(tag)
print(asset_url)
PY
)"
  version="$(echo "$release_info" | sed -n '1p')"
  asset_url="$(echo "$release_info" | sed -n '2p')"

  [[ -n "$version" && -n "$asset_url" ]] || die "Failed to resolve Prometheus release information."

  local tmp_dir
  tmp_dir="$(mktemp -d)"

  curl -fsSL "$asset_url" -o "${tmp_dir}/prometheus.tar.gz"
  tar -xzf "${tmp_dir}/prometheus.tar.gz" -C "$tmp_dir"

  local extracted_dir
  extracted_dir="$(find "$tmp_dir" -maxdepth 1 -type d -name "prometheus-*linux-${arch}" | head -n1)"
  [[ -n "$extracted_dir" ]] || die "Prometheus archive extraction failed."

  if ! id -u prometheus >/dev/null 2>&1; then
    run_root useradd --system --no-create-home --shell /usr/sbin/nologin prometheus
  fi

  run_root install -m 0755 "${extracted_dir}/prometheus" /usr/local/bin/prometheus
  run_root install -m 0755 "${extracted_dir}/promtool" /usr/local/bin/promtool
  run_root mkdir -p /etc/prometheus /etc/prometheus/consoles /etc/prometheus/console_libraries /var/lib/prometheus
  run_root cp -r "${extracted_dir}/consoles/." /etc/prometheus/consoles/
  run_root cp -r "${extracted_dir}/console_libraries/." /etc/prometheus/console_libraries/

  if [[ -f "${APP_DIR}/docker/prometheus.yml" ]]; then
    run_root cp "${APP_DIR}/docker/prometheus.yml" /etc/prometheus/prometheus.yml
  else
    run_root tee /etc/prometheus/prometheus.yml >/dev/null <<'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
EOF
  fi

  run_root chown -R prometheus:prometheus /etc/prometheus /var/lib/prometheus

  run_root tee /etc/systemd/system/prometheus.service >/dev/null <<EOF
[Unit]
Description=Prometheus Monitoring
After=network-online.target
Wants=network-online.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/usr/local/bin/prometheus \\
  --config.file=/etc/prometheus/prometheus.yml \\
  --storage.tsdb.path=/var/lib/prometheus \\
  --web.console.templates=/etc/prometheus/consoles \\
  --web.console.libraries=/etc/prometheus/console_libraries
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  run_root systemctl daemon-reload
  run_root systemctl enable --now prometheus

  rm -rf "$tmp_dir"
  log "Prometheus ${version} installed."
}

init_postgres_service() {
  detect_pkg_mgr
  if [[ "$PKG_MGR" == "dnf" ]]; then
    if [[ ! -f /var/lib/pgsql/data/PG_VERSION ]]; then
      log "Initializing PostgreSQL data directory (RHEL)."
      run_root postgresql-setup --initdb
    fi
  fi
  run_root systemctl enable --now postgresql
}

ensure_service_user_and_paths() {
  log "Ensuring service user and directories."

  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    run_root useradd --system --create-home --home-dir "${INSTALL_ROOT}" --shell /bin/bash "${SERVICE_USER}"
  fi

  run_root mkdir -p "${INSTALL_ROOT}" "${DATA_DIR}" "${APP_DIR}"

  if [[ "$(realpath "${SOURCE_DIR}")" != "$(realpath "${APP_DIR}")" ]]; then
    log "Syncing source to ${APP_DIR}."
    run_root rsync -a --delete \
      --exclude ".git/" \
      --exclude ".venv/" \
      --exclude "__pycache__/" \
      "${SOURCE_DIR}/" "${APP_DIR}/"
  else
    log "Source directory already at ${APP_DIR}, skipping sync."
  fi

  run_root chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_ROOT}"
}

create_python_venv() {
  log "Creating Python virtual environment and installing dependencies."
  run_as_user "${SERVICE_USER}" "
    cd '${APP_DIR}'
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip wheel
    pip install -r requirements.txt
    pip install gunicorn
  "
}

create_postgres_role_and_dbs() {
  log "Creating/updating PostgreSQL role and databases."
  local db_user_lit db_user_ident db_pw_lit ops_db_lit ops_db_ident gdb_lit gdb_ident
  db_user_lit="$(escape_sql_literal "${DB_USER}")"
  db_user_ident="$(escape_sql_ident "${DB_USER}")"
  db_pw_lit="$(escape_sql_literal "${DB_PASSWORD}")"
  ops_db_lit="$(escape_sql_literal "${OPS_DB_NAME}")"
  ops_db_ident="$(escape_sql_ident "${OPS_DB_NAME}")"
  gdb_lit="$(escape_sql_literal "${GRAFANA_DB_NAME}")"
  gdb_ident="$(escape_sql_ident "${GRAFANA_DB_NAME}")"

  run_as_postgres "
psql -v ON_ERROR_STOP=1 --dbname postgres <<'SQL'
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${db_user_lit}') THEN
    CREATE ROLE \"${db_user_ident}\" LOGIN PASSWORD '${db_pw_lit}';
  ELSE
    ALTER ROLE \"${db_user_ident}\" WITH LOGIN PASSWORD '${db_pw_lit}';
  END IF;
END
\$\$;
SQL
"

  if ! run_as_postgres "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${ops_db_lit}'\" | grep -q 1"; then
    run_as_postgres "psql --dbname postgres -c \"CREATE DATABASE \\\"${ops_db_ident}\\\" OWNER \\\"${db_user_ident}\\\";\""
  fi

  if ! run_as_postgres "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${gdb_lit}'\" | grep -q 1"; then
    run_as_postgres "psql --dbname postgres -c \"CREATE DATABASE \\\"${gdb_ident}\\\" OWNER \\\"${db_user_ident}\\\";\""
  fi

  run_as_postgres "psql --dbname postgres -c \"GRANT ALL PRIVILEGES ON DATABASE \\\"${ops_db_ident}\\\" TO \\\"${db_user_ident}\\\";\""
  run_as_postgres "psql --dbname postgres -c \"GRANT ALL PRIVILEGES ON DATABASE \\\"${gdb_ident}\\\" TO \\\"${db_user_ident}\\\";\""
}

write_env_file() {
  log "Writing ${ENV_FILE}."
  run_root mkdir -p "$(dirname "${ENV_FILE}")"
  run_root tee "${ENV_FILE}" >/dev/null <<EOF
# Required
FLASK_SECRET_KEY=${FLASK_SECRET_KEY}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=127.0.0.1
DB_NAME=${OPS_DB_NAME}

# Recommended
NMS_SECRET_KEY=${NMS_SECRET_KEY}
GRAFANA_BASE_URL=${GRAFANA_BASE_URL}
AUTOINTER_CACHE_DB=${DATA_DIR}/.servers_cache.db
AUTOINTER_DESKTOP_CACHE_DB=${DATA_DIR}/.desktops_cache.db
PROMETHEUS_URL=http://127.0.0.1:9090
INFLUXDB_URL=http://127.0.0.1:8086/query
INFLUXDB_DB=autointelli

# Optional bootstrap/admin convenience
ADMIN_PASSWORD=${ADMIN_PASSWORD}
SUPERADMIN_USERNAME=
SUPERADMIN_PASSWORD=

# Cookie behavior (reverse proxy + HTTPS)
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=None
EOF
  run_root chown "root:${SERVICE_USER}" "${ENV_FILE}"
  run_root chmod 640 "${ENV_FILE}"
}

init_app_schema_and_bootstrap() {
  log "Initializing Unified360 schema."
  run_as_user "${SERVICE_USER}" "
    cd '${APP_DIR}'
    source .venv/bin/activate
    set -a
    source '${ENV_FILE}'
    set +a
    python - <<'PY'
from app import app
from extensions import db
with app.app_context():
    db.create_all()
    print('Schema created')
PY
  "

  if [[ "${SKIP_BOOTSTRAP}" -eq 0 ]]; then
    log "Running bootstrap.py."
    run_as_user "${SERVICE_USER}" "
      cd '${APP_DIR}'
      source .venv/bin/activate
      set -a
      source '${ENV_FILE}'
      set +a
      python bootstrap.py
    "
  else
    log "Skipping bootstrap.py (--skip-bootstrap)."
  fi
}

import_grafana_sql() {
  [[ "${SKIP_GRAFANA_IMPORT}" -eq 1 ]] && {
    log "Skipping grafana.sql import (--skip-grafana-import)."
    return
  }

  if [[ ! -f "${APP_DIR}/grafana.sql" ]]; then
    warn "grafana.sql not found at ${APP_DIR}/grafana.sql, skipping import."
    return
  fi

  log "Importing grafana.sql into database ${GRAFANA_DB_NAME}."
  local db_user_ident db_name_ident db_pw_lit
  db_user_ident="$(escape_sql_ident "${DB_USER}")"
  db_name_ident="$(escape_sql_ident "${GRAFANA_DB_NAME}")"
  db_pw_lit="$(printf "%s" "${DB_PASSWORD}" | sed "s/'/'\\\\''/g")"

  run_as_postgres "
    PGPASSWORD='${db_pw_lit}' psql -h 127.0.0.1 -U \"${db_user_ident}\" -d \"${db_name_ident}\" -f '${APP_DIR}/grafana.sql'
  "
}

write_systemd_units() {
  log "Writing systemd units."
  run_root tee /etc/systemd/system/unified360-web.service >/dev/null <<EOF
[Unit]
Description=Unified360 Web App
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/gunicorn -w 4 -b 127.0.0.1:5050 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  run_root tee /etc/systemd/system/unified360-alert.service >/dev/null <<EOF
[Unit]
Description=Unified360 Alert Engine
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/alert_engine_service.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  run_root systemctl daemon-reload
  run_root systemctl enable --now unified360-web unified360-alert
}

configure_grafana_and_nginx() {
  [[ "${WITH_GRAFANA_STACK}" -eq 0 ]] && {
    log "Skipping Grafana + Nginx setup (--no-grafana-stack)."
    return
  }

  [[ -f "${APP_DIR}/deploy/grafana.ini" ]] || die "Missing deploy/grafana.ini."
  [[ -f "${APP_DIR}/deploy/nginx_grafana.conf" ]] || die "Missing deploy/nginx_grafana.conf."

  log "Configuring Grafana."
  run_root cp "${APP_DIR}/deploy/grafana.ini" /etc/grafana/grafana.ini
  run_root sed -i "s/^domain = .*/domain = ${DOMAIN}/" /etc/grafana/grafana.ini
  run_root sed -i "s|^root_url = .*|root_url = https://${DOMAIN}/grafana/|" /etc/grafana/grafana.ini
  run_root sed -i "s/^name = .*/name = ${GRAFANA_DB_NAME}/" /etc/grafana/grafana.ini
  run_root sed -i "s/^user = .*/user = ${DB_USER}/" /etc/grafana/grafana.ini
  run_root sed -i "s/^password = .*/password = ${DB_PASSWORD}/" /etc/grafana/grafana.ini
  run_root systemctl enable --now grafana-server
  run_root systemctl restart grafana-server

  log "Configuring Nginx."
  local nginx_conf="/etc/nginx/conf.d/${DOMAIN}.conf"
  run_root cp "${APP_DIR}/deploy/nginx_grafana.conf" "${nginx_conf}"
  run_root sed -i "s/performance\\.speedcloud\\.co\\.in/${DOMAIN}/g" "${nginx_conf}"

  run_root systemctl enable --now nginx
  run_root nginx -t
  run_root systemctl reload nginx

  if [[ "${WITH_CERTBOT}" -eq 1 ]]; then
    log "Running certbot for ${DOMAIN}."
    run_root certbot --nginx --non-interactive --agree-tos -m "${CERTBOT_EMAIL}" -d "${DOMAIN}"
    run_root nginx -t
    run_root systemctl reload nginx
  fi
}

docker_upsert_env() {
  local file="$1"
  local key="$2"
  local value="$3"

  if grep -qE "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >>"$file"
  fi
}

install_docker_mode() {
  has_cmd docker || die "docker command not found."
  docker compose version >/dev/null 2>&1 || die "docker compose plugin not available."

  local env_file="${SOURCE_DIR}/.env.docker"
  if [[ ! -f "$env_file" ]]; then
    cp "${SOURCE_DIR}/.env.docker.example" "$env_file"
  fi

  docker_upsert_env "$env_file" "DB_USER" "$DB_USER"
  docker_upsert_env "$env_file" "DB_PASSWORD" "$DB_PASSWORD"
  docker_upsert_env "$env_file" "DB_NAME" "$OPS_DB_NAME"
  docker_upsert_env "$env_file" "FLASK_SECRET_KEY" "$FLASK_SECRET_KEY"
  docker_upsert_env "$env_file" "NMS_SECRET_KEY" "$NMS_SECRET_KEY"
  docker_upsert_env "$env_file" "ADMIN_PASSWORD" "$ADMIN_PASSWORD"
  docker_upsert_env "$env_file" "GRAFANA_BASE_URL" "$GRAFANA_BASE_URL"

  log "Starting docker compose stack."
  if [[ "${WITH_METRICS}" -eq 1 ]]; then
    docker compose --env-file "$env_file" --profile metrics up -d --build
  else
    docker compose --env-file "$env_file" up -d --build
  fi

  log "Docker install completed."
  log "Open: http://localhost:5050/login"
  if [[ "${WITH_METRICS}" -eq 1 ]]; then
    log "Metrics endpoints: http://localhost:8086 (InfluxDB), http://localhost:9090 (Prometheus)"
  fi
}

native_install() {
  install_base_packages
  detect_pkg_mgr
  install_grafana_pkg
  init_postgres_service
  ensure_service_user_and_paths
  install_influxdb_native
  install_prometheus_native
  create_python_venv
  create_postgres_role_and_dbs
  write_env_file
  init_app_schema_and_bootstrap
  import_grafana_sql
  write_systemd_units
  configure_grafana_and_nginx

  log "Native install completed."
  log "Unified360 URL: https://${DOMAIN}/ (if Nginx/TLS configured) or http://127.0.0.1:5050/login"
  if [[ "${WITH_METRICS}" -eq 1 ]]; then
    log "Metrics endpoints: http://127.0.0.1:8086 (InfluxDB), http://127.0.0.1:9090 (Prometheus)"
  fi
}

main() {
  parse_args "$@"
  prepare_privilege_model
  validate_inputs

  if [[ "$MODE" == "docker" ]]; then
    install_docker_mode
  else
    native_install
  fi
}

main "$@"
