# Unified360 Installation Guide (Ubuntu 24.04 + RHEL 9)

This guide installs Unified360 on a fresh Linux host with:
- Web application (`app.py`)
- Alert engine worker (`alert_engine_service.py`)
- PostgreSQL backend
- InfluxDB OSS `1.11.x`
- Prometheus `latest`
- Grafana + Nginx auth-proxy integration (optional but recommended for production)

## 0. Automated Install (install.sh)

From project root:

```bash
chmod +x install.sh
sudo ./install.sh --db-password 'CHANGE_ME_STRONG_PASSWORD' --admin-password 'ChangeMeAdmin@123'
```

The native script above installs:
- Unified360
- PostgreSQL
- InfluxDB `1.11.x`
- Prometheus `latest`
- Grafana + Nginx (unless `--no-grafana-stack`)
- Seeds roles/permissions and `admin` user
- Does not create test users by default (`CREATE_TEST_USERS=false`)

Docker mode:

```bash
./install.sh --mode docker --db-password 'CHANGE_ME_STRONG_PASSWORD'
```

Docker script defaults to full stack with metrics profile enabled. Use `--no-metrics` to skip InfluxDB/Prometheus.

## 1. System Prerequisites

Use a host with internet access and sudo privileges.

### Ubuntu 24.04

```bash
sudo apt update
sudo apt install -y \
  git curl \
  python3 python3-venv python3-pip python3-dev \
  gcc libffi-dev libssl-dev libpq-dev \
  postgresql postgresql-contrib
```

### RHEL 9

```bash
sudo dnf -y update
sudo dnf install -y \
  git curl \
  python3 python3-pip python3-devel \
  gcc libffi-devel openssl-devel postgresql-devel \
  postgresql-server postgresql
```

Initialize PostgreSQL on RHEL 9:

```bash
sudo postgresql-setup --initdb
```

Enable/start PostgreSQL on both OSes:

```bash
sudo systemctl enable --now postgresql
```

## 2. Create Service User and Directories

```bash
sudo useradd --system --create-home --home-dir /opt/unified360 --shell /bin/bash unified360
sudo mkdir -p /opt/unified360/data
sudo chown -R unified360:unified360 /opt/unified360
```

## 3. Clone Source Code

```bash
sudo -u unified360 git clone <YOUR_REPO_URL> /opt/unified360/app
cd /opt/unified360/app
```

## 4. Create Python Virtual Environment

```bash
sudo -u unified360 bash -lc '
cd /opt/unified360/app
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
pip install gunicorn
'
```

## 5. Create PostgreSQL Database

Open PostgreSQL shell:

```bash
sudo -u postgres psql
```

Run:

```sql
CREATE USER autointelli WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
CREATE DATABASE opsduty OWNER autointelli;
GRANT ALL PRIVILEGES ON DATABASE opsduty TO autointelli;
CREATE DATABASE autointelli OWNER autointelli;
GRANT ALL PRIVILEGES ON DATABASE autointelli TO autointelli;
\q
```

## 6. Create Environment File

Create `/etc/unified360/unified360.env`:

```bash
sudo mkdir -p /etc/unified360
sudo tee /etc/unified360/unified360.env >/dev/null <<'EOF'
# Required
FLASK_SECRET_KEY=CHANGE_ME_TO_A_LONG_RANDOM_SECRET
DB_USER=autointelli
DB_PASSWORD=CHANGE_ME_STRONG_PASSWORD
DB_HOST=127.0.0.1
DB_NAME=opsduty

# Recommended
NMS_SECRET_KEY=CHANGE_ME_SQL_ENCRYPTION_SECRET
GRAFANA_BASE_URL=https://performance.speedcloud.co.in/grafana
AUTOINTER_CACHE_DB=/opt/unified360/data/.servers_cache.db
AUTOINTER_DESKTOP_CACHE_DB=/opt/unified360/data/.desktops_cache.db
PROMETHEUS_URL=http://127.0.0.1:9090
INFLUXDB_URL=http://127.0.0.1:8086/query
INFLUXDB_DB=autointelli

# Optional cloud/OT discovery seed config
ITAM_AWS_ACCOUNTS_JSON=
ITAM_AWS_REGIONS=us-east-1
ITAM_AZURE_SUBSCRIPTIONS_JSON=
ITAM_GCP_PROJECTS_JSON=
ITAM_OT_ASSETS_JSON=
ITAM_OT_MODBUS_ENDPOINTS_JSON=
ITAM_OT_BACNET_ENDPOINTS_JSON=
ITAM_OT_OPCUA_ENDPOINTS_JSON=

# Optional bootstrap/admin convenience
ADMIN_PASSWORD=ChangeMeAdmin@123
SUPERADMIN_USERNAME=
SUPERADMIN_PASSWORD=
EOF
```

Set permissions:

```bash
sudo chown root:unified360 /etc/unified360/unified360.env
sudo chmod 640 /etc/unified360/unified360.env
```

## 7. Initialize Unified360 Database and Seed RBAC

This repository currently does not include a `migrations/` folder, so initialize schema from SQLAlchemy models:

```bash
sudo -u unified360 bash -lc '
cd /opt/unified360/app
source .venv/bin/activate
set -a
source /etc/unified360/unified360.env
set +a
python - << "PY"
from app import app
from extensions import db
with app.app_context():
    db.create_all()
    print("Schema created")
PY
python bootstrap.py
'
```

Default bootstrap admin username is `admin` (password from `ADMIN_PASSWORD`).

### 7.1 Load Predefined Grafana PostgreSQL Schema/Data

This repository includes a prebuilt dump at project root: `grafana.sql`.

Import it into PostgreSQL Grafana DB (`autointelli`):

```bash
cd /opt/unified360/app
PGPASSWORD='CHANGE_ME_STRONG_PASSWORD' psql -h 127.0.0.1 -U autointelli -d autointelli -f grafana.sql
```

## 8. Run Once Manually (Smoke Test)

```bash
sudo -u unified360 bash -lc '
cd /opt/unified360/app
source .venv/bin/activate
set -a
source /etc/unified360/unified360.env
set +a
python app.py
'
```

Open `http://127.0.0.1:5050/login` on the server (or through SSH tunnel/reverse proxy), verify login works, then stop with `Ctrl+C`.

## 9. Create systemd Services

Create web service `/etc/systemd/system/unified360-web.service`:

```ini
[Unit]
Description=Unified360 Web App
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=unified360
Group=unified360
WorkingDirectory=/opt/unified360/app
EnvironmentFile=/etc/unified360/unified360.env
ExecStart=/opt/unified360/app/.venv/bin/gunicorn -w 4 -b 127.0.0.1:5050 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create alert worker service `/etc/systemd/system/unified360-alert.service`:

```ini
[Unit]
Description=Unified360 Alert Engine
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=unified360
Group=unified360
WorkingDirectory=/opt/unified360/app
EnvironmentFile=/etc/unified360/unified360.env
ExecStart=/opt/unified360/app/.venv/bin/python /opt/unified360/app/alert_engine_service.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create ITAM scheduler service `/etc/systemd/system/unified360-itam-discovery.service`:

```ini
[Unit]
Description=Unified360 ITAM Discovery Scheduler
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=unified360
Group=unified360
WorkingDirectory=/opt/unified360/app
EnvironmentFile=/etc/unified360/unified360.env
ExecStart=/opt/unified360/app/.venv/bin/python /opt/unified360/app/itam_discovery_service.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now unified360-web unified360-alert unified360-itam-discovery
sudo systemctl status unified360-web --no-pager
sudo systemctl status unified360-alert --no-pager
sudo systemctl status unified360-itam-discovery --no-pager
```

### 9.1 Install InfluxDB OSS 1.11.x (Native)

Ubuntu 24.04:

```bash
sudo apt-get update
sudo apt-get install -y curl gnupg2 ca-certificates
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://repos.influxdata.com/influxdata-archive_compat.key | sudo gpg --dearmor -o /etc/apt/keyrings/influxdata-archive_compat.gpg
echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main' | sudo tee /etc/apt/sources.list.d/influxdata.list >/dev/null
sudo apt-get update
INFLUX_VER="$(apt-cache madison influxdb | awk '{print $3}' | grep -E '^1\.11\.' | sort -V | tail -n1)"
sudo apt-get install -y "influxdb=${INFLUX_VER}"
```

RHEL 9:

```bash
sudo tee /etc/yum.repos.d/influxdata.repo >/dev/null <<'EOF'
[influxdata]
name=InfluxData Repository - Stable
baseurl=https://repos.influxdata.com/rhel/$releasever/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://repos.influxdata.com/influxdata-archive_compat.key
EOF
sudo dnf clean all
sudo dnf makecache -y
sudo dnf install -y "influxdb-1.11*"
```

Enable service and create required DBs:

```bash
sudo systemctl enable --now influxdb
influx -host 127.0.0.1 -port 8086 -execute 'CREATE DATABASE "autointelli"'
influx -host 127.0.0.1 -port 8086 -execute 'CREATE DATABASE "fortigate"'
influx -host 127.0.0.1 -port 8086 -execute 'CREATE DATABASE "end_user_monitoring"'
```

### 9.2 Install Prometheus Latest (Native)

```bash
ARCH="$(uname -m)"
if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then PROM_ARCH="amd64"; else PROM_ARCH="arm64"; fi

readarray -t REL < <(python3 - "$PROM_ARCH" <<'PY'
import json, sys, urllib.request
arch = sys.argv[1]
req = urllib.request.Request("https://api.github.com/repos/prometheus/prometheus/releases/latest", headers={"User-Agent":"unified360-installer"})
with urllib.request.urlopen(req, timeout=30) as r:
    d = json.load(r)
needle = f"linux-{arch}.tar.gz"
url = next(a["browser_download_url"] for a in d["assets"] if a["name"].endswith(needle))
print(d["tag_name"].lstrip("v"))
print(url)
PY
)
VER="${REL[0]}"
URL="${REL[1]}"
TMP="$(mktemp -d)"
curl -fsSL "$URL" -o "$TMP/prometheus.tar.gz"
tar -xzf "$TMP/prometheus.tar.gz" -C "$TMP"
DIR="$(find "$TMP" -maxdepth 1 -type d -name "prometheus-*linux-${PROM_ARCH}" | head -n1)"

sudo useradd --system --no-create-home --shell /usr/sbin/nologin prometheus 2>/dev/null || true
sudo install -m 0755 "$DIR/prometheus" /usr/local/bin/prometheus
sudo install -m 0755 "$DIR/promtool" /usr/local/bin/promtool
sudo mkdir -p /etc/prometheus /etc/prometheus/consoles /etc/prometheus/console_libraries /var/lib/prometheus
sudo cp -r "$DIR/consoles/." /etc/prometheus/consoles/
sudo cp -r "$DIR/console_libraries/." /etc/prometheus/console_libraries/
sudo cp /opt/unified360/app/docker/prometheus.yml /etc/prometheus/prometheus.yml
sudo chown -R prometheus:prometheus /etc/prometheus /var/lib/prometheus
rm -rf "$TMP"
```

Create service:

```bash
sudo tee /etc/systemd/system/prometheus.service >/dev/null <<'EOF'
[Unit]
Description=Prometheus Monitoring
After=network-online.target
Wants=network-online.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/usr/local/bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/var/lib/prometheus \
  --web.console.templates=/etc/prometheus/consoles \
  --web.console.libraries=/etc/prometheus/console_libraries
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now prometheus
```

## 10. Post-Install Validation

```bash
sudo journalctl -u unified360-web -n 100 --no-pager
sudo journalctl -u unified360-alert -n 100 --no-pager
sudo journalctl -u unified360-itam-discovery -n 100 --no-pager
sudo journalctl -u influxdb -n 100 --no-pager
sudo journalctl -u prometheus -n 100 --no-pager
```

Check app login at:
- `http://127.0.0.1:5050/login` from the server
- or via your reverse proxy

## 11. Grafana + Nginx Auth-Proxy (Production Setup)

This project includes your working configs:
- `deploy/grafana.ini`
- `deploy/nginx_grafana.conf`

### 11.1 Install Grafana + Nginx

Ubuntu 24.04:

```bash
sudo apt update
sudo apt install -y grafana nginx certbot python3-certbot-nginx
```

RHEL 9 (Grafana repo + install):

```bash
sudo tee /etc/yum.repos.d/grafana.repo >/dev/null <<'EOF'
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
sudo dnf install -y grafana nginx certbot python3-certbot-nginx
```

### 11.2 Apply Grafana Configuration

```bash
cd /opt/unified360/app
sudo cp deploy/grafana.ini /etc/grafana/grafana.ini
sudo systemctl enable --now grafana-server
sudo systemctl restart grafana-server
sudo systemctl status grafana-server --no-pager
```

This config uses:
- Grafana at `https://performance.speedcloud.co.in/grafana/`
- PostgreSQL database `autointelli` on `127.0.0.1:5432`
- Auth proxy header `X-WEBAUTH-USER`

### 11.3 Apply Nginx Configuration

```bash
cd /opt/unified360/app
sudo cp deploy/nginx_grafana.conf /etc/nginx/conf.d/performance.speedcloud.co.in.conf
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

### 11.4 Provision TLS Certificate

```bash
sudo certbot --nginx -d performance.speedcloud.co.in
sudo nginx -t
sudo systemctl reload nginx
```

### 11.5 Validate Grafana SSO Proxy Flow

- Ensure Unified360 is running at `127.0.0.1:5050`.
- Open `https://performance.speedcloud.co.in/` and login to Unified360.
- Open any monitor page and click Grafana dashboard button.
- Verify Grafana opens at `https://performance.speedcloud.co.in/grafana/...` without separate login prompt.

## 12. Notes

- For production, keep Unified360 behind TLS reverse proxy.
- If you want direct network access without reverse proxy, change gunicorn bind to `0.0.0.0:5050`.
- Keep `GRAFANA_BASE_URL=https://performance.speedcloud.co.in/grafana` in Unified360 environment.
- Ensure Prometheus/InfluxDB/Grafana endpoints are reachable from this host.
- If credentials were temporary, rotate:
  - PostgreSQL user password
  - `FLASK_SECRET_KEY`
  - `NMS_SECRET_KEY`
  - admin/superadmin credentials
  - Grafana DB password in `/etc/grafana/grafana.ini`

## 13. Docker Option (Alternative Install Path)

This repository now includes:
- `Dockerfile`
- `docker-compose.yml`
- `.env.docker.example`
- `docker/entrypoint-web.sh`
- `docker/entrypoint-alert.sh`
- `docker/prometheus.yml`

### 13.1 Prerequisite

Install Docker Engine + Docker Compose plugin on your host.

Verify:

```bash
docker --version
docker compose version
```

### 13.2 Configure Environment

From repo root:

```bash
cp .env.docker.example .env.docker
```

Edit `.env.docker` and set at minimum:
- `FLASK_SECRET_KEY`
- `NMS_SECRET_KEY`
- `DB_PASSWORD`
- `ADMIN_PASSWORD`
- `GRAFANA_BASE_URL` (browser-reachable URL)

### 13.3 Start Full Stack (Default: Includes Metrics)

```bash
docker compose --env-file .env.docker --profile metrics up -d --build
```

This starts:
- `unified360-postgres`
- `unified360-web`
- `unified360-alert`
- `unified360-itam-discovery`
- `unified360-influxdb` (InfluxDB `1.11.8`)
- `unified360-prometheus` (Prometheus `latest`)
- `unified360-grafana`

The web entrypoint automatically:
1. Waits for PostgreSQL.
2. Runs `db.create_all()`.
3. Runs `bootstrap.py` when `AUTO_BOOTSTRAP=1`.

PostgreSQL init also automatically:
1. Creates Grafana DB `autointelli`.
2. Imports root dump `grafana.sql` into `autointelli`.

### 13.4 Start Core Stack Without Metrics (Optional)

```bash
docker compose --env-file .env.docker up -d --build
```

This starts only:
- `unified360-postgres`
- `unified360-web`
- `unified360-alert`
- `unified360-itam-discovery`

### 13.5 Validate

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f web
docker compose --env-file .env.docker logs -f alert
docker compose --env-file .env.docker logs -f itam-discovery
docker compose --env-file .env.docker logs -f influxdb
docker compose --env-file .env.docker logs -f prometheus
```

Open:
- `http://localhost:5050/login`

### 13.6 Stop / Cleanup

Stop only:

```bash
docker compose --env-file .env.docker down
```

Stop and remove volumes (destructive):

```bash
docker compose --env-file .env.docker down -v
```

### 13.7 Docker Notes

- `SESSION_COOKIE_SECURE=false` is default in `.env.docker.example` for local HTTP testing.
- For production behind HTTPS, set:
  - `SESSION_COOKIE_SECURE=true`
  - `SESSION_COOKIE_SAMESITE=None` (if cross-site cookie behavior is needed)
- Persisted data is stored in Docker volumes (`pg_data`, `app_data`, etc.).
- InfluxDB image tag is pinned to `1.11.8` (`1.11.x` family).
- Prometheus image tag is `latest`.
- If `pg_data` already existed before Grafana init was added, import manually:

```bash
docker exec -it unified360-postgres psql -U autointelli -d postgres -c "CREATE DATABASE autointelli OWNER autointelli;"
cat grafana.sql | docker exec -i unified360-postgres psql -U autointelli -d autointelli
```
