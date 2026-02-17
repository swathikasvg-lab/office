import security
from security import login_required_api
from flask import Blueprint, jsonify
from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from extensions import db
from models.customer import Customer
from models.device_status_alert import DeviceStatusAlert
from models.alert_rule_state import AlertRuleState
from models.ping import PingConfig
from models.port_monitor import PortMonitor
from models.url_monitor import UrlMonitor
from models.snmp import SnmpConfig
from models.idrac import IdracConfig
from models.link_monitor import LinkMonitor
from models.proxy import ProxyServer
from routes.desktop_routes import get_db_conn, read_cache_all

customer_dash_bp = Blueprint("customer_dash", __name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------- KPI COMPUTATION ----------------
def compute_customer_kpis(customer_id):
    total_ping = PingConfig.query.filter_by(customer_id=customer_id).count()
    total_port = PortMonitor.query.filter_by(customer_id=customer_id).count()
    total_url  = UrlMonitor.query.filter_by(customer_id=customer_id).count()
    total_snmp = SnmpConfig.query.filter_by(customer_id=customer_id).count()
    total_idrac = IdracConfig.query.filter_by(customer_id=customer_id).count()
    total_link = LinkMonitor.query.filter_by(customer_id=customer_id).count()
    total_proxy = ProxyServer.query.filter_by().count()

    total = sum([
        total_ping, total_port, total_url,
        total_snmp, total_idrac, total_link, total_proxy
    ])

    if hasattr(DeviceStatusAlert, "customer_id"):
        down = (
            db.session.query(func.count(DeviceStatusAlert.id))
            .filter(
                DeviceStatusAlert.customer_id == customer_id,
                DeviceStatusAlert.is_active == True,
                DeviceStatusAlert.last_status == "DOWN"
            )
            .scalar()
            or 0
        )
    else:
        down = 0

    active = max(0, total - down)
    health = int((active / total) * 100) if total else 100

    critical_alerts = AlertRuleState.query.filter(
        AlertRuleState.customer_id == customer_id,
        AlertRuleState.is_active == True
    ).count()

    return {
        "total": total,
        "active": active,
        "health_percent": health,
        "critical_alerts": critical_alerts
    }


# ---------------- HEATMAP ----------------
def build_customer_heatmap(customer_id):
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    now_ist = now_utc.astimezone(IST)
    since_utc = now_utc - timedelta(hours=24)

    slots = [
        now_ist - timedelta(minutes=30 * (47 - i))
        for i in range(48)
    ]

    categories = [
        "Servers", "Desktops", "Ping",
        "Port", "URL", "SNMP", "iDRAC", "Link", "Proxy"
    ]

    matrix = [[0 for _ in range(len(slots))] for _ in categories]

    if hasattr(DeviceStatusAlert, "customer_id"):
        alerts = DeviceStatusAlert.query.filter(
            DeviceStatusAlert.customer_id == customer_id,
            DeviceStatusAlert.updated_at >= since_utc
        ).all()
    else:
        alerts = []

    for a in alerts:
        t = a.last_change or a.updated_at
        if not t:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        t = t.astimezone(IST)

        for si, s in enumerate(slots):
            if s <= t < s + timedelta(minutes=30):
                src = (a.source or "").lower()
                if "ping" in src: r = 2
                elif "port" in src: r = 3
                elif "url" in src: r = 4
                elif "snmp" in src: r = 5
                elif "idrac" in src: r = 6
                elif "link" in src: r = 7
                elif "proxy" in src: r = 8
                else: r = 0

                if a.last_status == "DOWN" and a.is_active:
                    matrix[r][si] = 2
                break

    return {
        "categories": categories,
        "timestamps": [s.strftime("%H:%M") for s in slots],
        "matrix": matrix
    }


# ---------------- MAIN API ----------------
@customer_dash_bp.route("/api/dashboard2/customer-overview")
@login_required_api
def api_customer_overview():
    user = security.get_current_user()
    allowed_cid = security.get_allowed_customer_id(user)  # None for global admin

    result = []

    if allowed_cid is None:
        customers = Customer.query.order_by(Customer.name).all()
    else:
        customers = Customer.query.filter(Customer.cid == allowed_cid).all()

    for c in customers:
        result.append({
            "customer_id": c.cid,
            "customer_name": c.name,
            "kpi": compute_customer_kpis(c.cid),
            "heatmap": build_customer_heatmap(c.cid)
        })

    return jsonify({"customers": result, "ok": True})

