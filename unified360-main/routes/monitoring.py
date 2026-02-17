from flask import Blueprint, jsonify, request
from datetime import datetime

from models.port_monitor import PortMonitor
from models.proxy import ProxyServer
from models.ping import PingConfig
from models.url_monitor import UrlMonitor
from models.idrac import IdracConfig
from models.snmp import SnmpConfig
from models.ilo import IloConfig
from models.sqlserver_monitor import SqlServerMonitor
from models.oracle_db_monitor import OracleDbMonitor

monitor_bp = Blueprint("monitor", __name__)


# =====================================================================
# Helper: Validate Monitoring Server (Proxy)
# =====================================================================
def _get_proxy_or_error(server_ip):
    """
    Ensures the requesting monitoring server is registered.
    Used across all sync APIs for OpsDuty Agents.
    """
    if not server_ip:
        return None, jsonify({"ok": False, "error": "server param required"}), 400

    proxy = ProxyServer.query.filter_by(ip_address=server_ip).first()
    if not proxy:
        return None, jsonify({"ok": False, "error": "unknown proxy server"}), 404

    return proxy, None, None


# =====================================================================
# 🔹 Sync HP iLO Monitoring Jobs
# =====================================================================
@monitor_bp.get("/api/monitoring/sync_ilo")
def api_sync_ilo():
    """
    Returns HP iLO configurations assigned to a monitoring server.
    Called by OpsDuty Agent.
    """
    server_ip = (request.args.get("server") or "").strip()
    proxy, err, code = _get_proxy_or_error(server_ip)
    if err:
        return err, code

    items = (
        IloConfig.query
        .filter_by(monitoring_server=server_ip)
        .order_by(IloConfig.id.asc())
        .all()
    )

    return jsonify({
        "ok": True,
        "server": server_ip,
        "count": len(items),
        "items": [x.to_dict(masked=False) for x in items]
    })


#
# Sync SQLSERVER
#
@monitor_bp.get("/api/monitoring/sync_sqlserver")
def api_sqlserver_sync_for_agent():
    """
    Agent sync endpoint (no session login).
    Returns monitors assigned to a monitoring server IP.

    NOTE: For production, protect this with an agent token or mTLS.
    """
    server_ip = (request.args.get("server") or "").strip()
    if not server_ip:
        return jsonify({"ok": False, "error": "Missing server"}), 400

    items = (
        SqlServerMonitor.query
        .filter(SqlServerMonitor.monitoring_server == server_ip)
        .filter(SqlServerMonitor.active.is_(True))
        .order_by(SqlServerMonitor.id.asc())
        .all()
    )

    # include_secret=True because the agent must render Telegraf SQL connection
    return jsonify({
        "ok": True,
        "items": [x.to_dict(include_secret=True) for x in items]
    })



# =====================================================================
# 🔹 Sync iDRAC Monitoring Jobs
# =====================================================================
@monitor_bp.get("/api/monitoring/sync_idrac")
def api_sync_idrac():
    """
    Returns iDRAC configurations assigned to a monitoring server.
    Called by OpsDuty Agent.
    """
    server_ip = (request.args.get("server") or "").strip()
    proxy, err, code = _get_proxy_or_error(server_ip)
    if err:
        return err, code

    items = (
        IdracConfig.query
        .filter_by(monitoring_server=server_ip)
        .order_by(IdracConfig.id.asc())
        .all()
    )

    return jsonify({
        "ok": True,
        "server": server_ip,
        "count": len(items),
        "items": [x.to_dict(masked=False) for x in items]
    })


# =====================================================================
# 🔹 Sync PORT Monitoring Jobs
# =====================================================================
@monitor_bp.get("/api/monitoring/sync")
def api_monitoring_sync_port():
    server_ip = request.args.get("server")
    proxy, err, code = _get_proxy_or_error(server_ip)
    if err:
        return err, code

    # Only active jobs
    q = PortMonitor.query.filter_by(monitoring_server=server_ip, active=True)
    items = [p.to_dict() for p in q.all()]

    return jsonify({
        "ok": True,
        "server": server_ip,
        "count": len(items),
        "items": items,
        "server_time": datetime.utcnow().isoformat() + "Z"
    })


# =====================================================================
# 🔹 Sync SNMP Monitoring Jobs
# =====================================================================
@monitor_bp.get("/api/monitoring/sync_snmp")
def api_snmp_sync():
    server_ip = request.args.get("server")
    proxy, err, code = _get_proxy_or_error(server_ip)
    if err:
        return err, code

    items = SnmpConfig.query.filter_by(monitoring_server=server_ip).all()

    return jsonify({
        "ok": True,
        "server": server_ip,
        "count": len(items),
        "items": [x.to_dict(masked=False) for x in items]
    })


# =====================================================================
# 🔹 Sync PING Monitoring Jobs
# =====================================================================
@monitor_bp.get("/api/monitoring/sync_ping")
def api_monitoring_sync_ping():
    """
    Returns ping checks configured for this monitoring server.
    """
    server_ip = request.args.get("server")
    proxy, err, code = _get_proxy_or_error(server_ip)
    if err:
        return err, code

    items = PingConfig.query.filter_by(monitoring_server=server_ip).all()

    return jsonify({
        "ok": True,
        "server": server_ip,
        "count": len(items),
        "items": [x.to_dict() for x in items],
        "server_time": datetime.utcnow().isoformat() + "Z"
    })


# =====================================================================
# 🔹 Sync URL Monitoring Jobs
# =====================================================================
@monitor_bp.get("/api/monitoring/sync_url")
def api_monitoring_sync_url():
    server_ip = request.args.get("server")
    proxy, err, code = _get_proxy_or_error(server_ip)
    if err:
        return err, code

    items = UrlMonitor.query.filter_by(monitoring_server=server_ip).all()

    return jsonify({
        "ok": True,
        "server": server_ip,
        "count": len(items),
        "items": [x.to_dict() for x in items],
        "server_time": datetime.utcnow().isoformat() + "Z"
    })

# =====================================================================
# 🔹 Sync Oracle DB Monitoring Jobs
# =====================================================================
@monitor_bp.get("/api/monitoring/sync_oracle")
def api_monitoring_sync_oracle():
    server_ip = request.args.get("server")
    proxy, err, code = _get_proxy_or_error(server_ip)
    if err:
        return err, code

    items = OracleDbMonitor.query.filter_by(monitoring_server=server_ip, active=True).all()

    return jsonify({
        "ok": True,
        "server": server_ip,
        "count": len(items),
        "items": [x.to_agent_dict() for x in items],
        "server_time": datetime.utcnow().isoformat() + "Z"
    })

