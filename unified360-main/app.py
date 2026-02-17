from flask import Flask, redirect, url_for, session, render_template
from urllib.parse import quote_plus
from functools import wraps
import sys
import os
import security
from security import get_current_user
from extensions import db
from flask_migrate import Migrate

# Models to ensure they are registered with SQLAlchemy
from models.ops_user import Ops_User, Role, Permission
from models.idrac import IdracConfig
from models.snmp import SnmpConfig
from models.smtp import SmtpConfig
from models.device_status_alert import DeviceStatusAlert
from models.alert_rule import AlertRule
from models.alert_rule_state import AlertRuleState
from models.contact import Contact, ContactGroup
from models.customer import Customer
from models.ping import PingConfig
from models.port_monitor import PortMonitor
from models.url_monitor import UrlMonitor
from models.proxy import ProxyServer
from models.link_monitor import LinkMonitor
from models.discovery import DiscoveredAsset, DiscoveryJob
from models.device_updown_rule import DeviceUpDownRule
from models.ilo import IloConfig
from models.sqlserver_monitor import SqlServerMonitor
from models.oracle_db_monitor import OracleDbMonitor
from models.license import License, LicenseItem
from models.itom import (
    BusinessApplication,
    ApplicationService,
    ServiceBinding,
    ServiceDependency,
)
from models.itom_layout import ItomGraphLayout
from models.copilot_audit import CopilotAuditLog
from models.remediation import Runbook, RemediationAction
from models.report_ai import ReportSchedule, ReportNarrative
from models.itam import (
    ItamAsset,
    ItamAssetIdentity,
    ItamAssetSource,
    ItamAssetSoftware,
    ItamAssetHardware,
    ItamAssetNetworkInterface,
    ItamAssetTag,
    ItamAssetLifecycle,
    ItamAssetRelation,
    ItamDiscoveryRun,
    ItamDiscoveryPolicy,
    ItamCloudIntegration,
    ItamCompliancePolicy,
    ItamComplianceRun,
    ItamComplianceFinding,
    ItamAssetItomBinding,
)
# Blueprints
from routes.auth_routes import auth_bp
from routes.idrac_routes import idrac_bp
from routes.url_routes import url_bp
from routes.snmp_routes import snmp_bp
from routes.smtp_routes import smtp_bp
from routes.ping_routes import ping_bp
from routes.proxy_routes import proxy_bp
from routes.contact_routes import contacts_bp
from routes.contact_group_routes import contact_groups_bp
from routes.alert_routes import alerts_bp
from routes.port_routes import port_bp
from routes.monitoring import monitor_bp
from routes.dashboard_routes import dashboard_bp
from routes.dashboard_customer_overview import customer_dash_bp
from routes.server_routes import server_bp
from routes.monitor_status_routes import monitor_status
from routes.tools_routes import tools_bp
from routes.link_routes import link_bp
from routes.discovery_routes import discovery_bp
from routes.desktop_routes import desktop_bp
from routes.report_routes import report_bp
from routes.customer_routes import customer_bp
from routes.admin_user_routes import admin_users_bp
from routes.device_updown_routes import device_updown_bp
from routes.ilo_routes import ilo_bp
from routes.iis_routes import iis_bp
from routes.sqlserver_routes import sqlserver_bp
from routes.oracle_routes import oracle_bp
from routes.licensing_routes import license_bp
from routes.itom_routes import itom_bp
from routes.copilot_routes import copilot_bp
from routes.remediation_routes import remediation_bp
from routes.report_ai_routes import report_ai_bp
from routes.itam_asset_routes import itam_assets_bp
# -----------------------------
# APP INITIALIZATION
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapped

app = Flask(__name__)
app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("SECRET_KEY")
    or os.urandom(32)
)

db_uri = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL")
if not db_uri:
    db_user = os.environ.get("DB_USER", "autointelli")
    db_pass = os.environ.get("DB_PASSWORD")
    db_host = os.environ.get("DB_HOST", "localhost")
    db_name = os.environ.get("DB_NAME", "opsduty")
    if db_pass:
        db_uri = f"postgresql://{db_user}:{quote_plus(db_pass)}@{db_host}/{db_name}"
    else:
        db_uri = f"sqlite:///{os.path.join(BASE_DIR, 'opsduty.db')}"

app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

#Grafana integration
app.config["SESSION_COOKIE_PATH"] = "/"
session_cookie_samesite = os.environ.get("SESSION_COOKIE_SAMESITE", "None")
if session_cookie_samesite not in {"Lax", "Strict", "None"}:
    session_cookie_samesite = "None"
app.config["SESSION_COOKIE_SAMESITE"] = session_cookie_samesite
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
app.config["GRAFANA_BASE_URL"] = (
    os.environ.get("GRAFANA_BASE_URL")
    or "https://performance.speedcloud.co.in/grafana"
).rstrip("/")

# Alerting Config
app.config["DEVICE_STALE_SECONDS"] = 300
app.config["INFLUXDB_URL"] = "http://localhost:8086/query"
app.config["INFLUXDB_DB"] = "autointelli"
app.config["PROMETHEUS_URL"] = "http://localhost:9090"

# NEW: default contact group for device up/down alerts
app.config["DEVICE_ALERT_CONTACT_GROUP_ID"] = 1  # change to your NOC group id
app.config["DEVICE_UPDOWN_DEFAULT_CONTACT_GROUP_ID"] = 1


# --- RBAC Helpers for Jinja ---
@app.context_processor
def inject_rbac():
    user = get_current_user()

    # Keep existing variables for UI compatibility
    roles = session.get("user", {}).get("roles", [])
    is_admin = session.get("user", {}).get("is_admin", False)

    def can(permission_code: str) -> bool:
        if not user:
            return False
        return security.has_permission(user, permission_code)

    return {
        "can": can,
        "user_roles": roles,     # preserved
        "is_admin": is_admin,    # preserved
        "GRAFANA_BASE_URL": app.config.get("GRAFANA_BASE_URL", "")
    }


# Init DB + Migration
db.init_app(app)
migrate = Migrate(app, db)

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(idrac_bp)
app.register_blueprint(url_bp)
app.register_blueprint(snmp_bp)
app.register_blueprint(smtp_bp)
app.register_blueprint(ping_bp)
app.register_blueprint(proxy_bp)
app.register_blueprint(contacts_bp)
app.register_blueprint(contact_groups_bp)
app.register_blueprint(alerts_bp)
app.register_blueprint(port_bp)
app.register_blueprint(monitor_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(server_bp)
app.register_blueprint(monitor_status)
app.register_blueprint(tools_bp)
app.register_blueprint(link_bp)
app.register_blueprint(discovery_bp)
app.register_blueprint(desktop_bp)
app.register_blueprint(report_bp)
app.register_blueprint(customer_bp)
app.register_blueprint(customer_dash_bp)
app.register_blueprint(admin_users_bp)
app.register_blueprint(device_updown_bp)
app.register_blueprint(ilo_bp)
app.register_blueprint(iis_bp)
app.register_blueprint(sqlserver_bp)
app.register_blueprint(oracle_bp)
app.register_blueprint(license_bp)
app.register_blueprint(itom_bp)
app.register_blueprint(copilot_bp)
app.register_blueprint(remediation_bp)
app.register_blueprint(report_ai_bp)
app.register_blueprint(itam_assets_bp)


app.jinja_env.globals["can"] = security.can


# Routes
@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("dashboard.dashboard_home"))
    return redirect(url_for("auth.login"))

@app.route('/alert/config')
@login_required
def alert_config():
    return render_template('alert_config.html')

@app.route('/smtp/config')
@login_required
def smtp_config():
    return render_template('smtp_config.html')


# Dev mode only
if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5050, debug=True)

