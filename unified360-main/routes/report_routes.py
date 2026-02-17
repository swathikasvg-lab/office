from flask import Blueprint, render_template, request, jsonify, send_file, session, redirect, url_for
from functools import wraps
from datetime import datetime
import os
from flask import current_app
from services.http_utils import get_json_with_retry

from reports.server.rpt_1001 import ServerAvailabilityReport
from reports.server.rpt_1002 import ServerPerformanceReport
from reports.desktop.rpt_1003 import DesktopPerformanceReport
from reports.url.rpt_1004 import UrlPerformanceReport
from reports.snmp.rpt_1005 import BandwidthUtilizationReport
from reports.ping.rpt_1006 import PingPerformanceReport
from reports.port.rpt_1007 import PortPerformanceReport
from reports.fortigate.rpt_1008 import FortigateVpnReport
from reports.fortigate.rpt_1009 import FortigateSdwanReport

report_bp = Blueprint("reports", __name__)


def _influx_query_json(influx_url, influx_db, query, timeout=20):
    return get_json_with_retry(
        influx_url,
        params={"db": influx_db, "q": query},
        timeout=timeout,
        retries=2,
    )


def _fortigate_influx_cfg():
    influx_url = (
        current_app.config.get("FORTIGATE_INFLUXDB_URL")
        or current_app.config.get("INFLUXDB_URL")
        or os.environ.get("FORTIGATE_INFLUXDB_URL")
        or os.environ.get("INFLUXDB_URL")
        or "http://127.0.0.1:8086/query"
    )
    influx_db = (
        current_app.config.get("FORTIGATE_INFLUXDB_DB")
        or os.environ.get("FORTIGATE_INFLUXDB_DB")
        or "fortigate"
    )
    return influx_url, influx_db


def _desktop_influx_cfg():
    influx_url = (
        current_app.config.get("DESKTOP_INFLUXDB_URL")
        or os.environ.get("DESKTOP_INFLUXDB_URL")
        or os.environ.get("INFLUXDB_URL")
        or "http://127.0.0.1:8086/query"
    )
    influx_db = (
        current_app.config.get("DESKTOP_INFLUXDB_DB")
        or os.environ.get("DESKTOP_INFLUXDB_DB")
        or "end_user_monitoring"
    )
    return influx_url, influx_db


# ============================================================
# AUTH HELPERS
# ============================================================
def _current_user():
    return session.get("user")


def require_login_page(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        return fn(*a, **kw)
    return wrapper


def require_login_api(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if "user" not in session:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(*a, **kw)
    return wrapper


def require_admin_api(fn):
    """
    All reporting endpoints reveal cross-customer data.
    Admin-only enforcement is necessary.
    """
    @wraps(fn)
    def wrapper(*a, **kw):
        user = _current_user()
        if not user or not user.get("is_admin"):
            return jsonify({"ok": False, "error": "Forbidden – Admin access required"}), 403
        return fn(*a, **kw)
    return wrapper


# ============================================================
# REPORT CONFIG PAGE
# ============================================================
@report_bp.get("/report_config")
@require_login_page
def report_config():
    return render_template("report_config.html")


# ============================================================
# FORTIGATE DEVICES
# ============================================================
@report_bp.get("/api/fortigate/devices")
@require_admin_api
def fortigate_devices():
    try:
        influx_url, influx_db = _fortigate_influx_cfg()

        query = """SHOW TAG VALUES FROM "vpn_tunnels" WITH KEY = "hostname" """
        data = _influx_query_json(influx_url, influx_db, query, timeout=20)

        devices = []
        result = data.get("results", [{}])[0]
        if "series" in result:
            for row in result["series"][0]["values"]:
                devices.append(row[1])

        return jsonify({"ok": True, "devices": sorted(set(devices))})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# PORT SERVERS
# ============================================================
@report_bp.get("/api/port/servers")
@require_admin_api
def port_servers():
    try:
        influx_url = current_app.config["INFLUXDB_URL"]
        influx_db = current_app.config["INFLUXDB_DB"]

        query = """
        SELECT DISTINCT("server")
        FROM (SELECT * FROM net_response WHERE time >= now() - 30d)
        """
        data = _influx_query_json(influx_url, influx_db, query, timeout=20)

        servers = []
        result = data.get("results", [{}])[0]
        if "series" in result:
            for row in result["series"][0]["values"]:
                servers.append(row[1])

        return jsonify({"ok": True, "servers": sorted(servers)})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# PORT -> PORT LIST
# ============================================================
@report_bp.get("/api/port/ports")
@require_admin_api
def port_ports():
    try:
        server = request.args.get("server")
        if not server:
            return jsonify({"ok": False, "error": "Missing server"}), 400

        influx_url = current_app.config["INFLUXDB_URL"]
        influx_db = current_app.config["INFLUXDB_DB"]

        query = f"""
        SELECT DISTINCT("port")
        FROM (
            SELECT * FROM net_response
            WHERE time >= now() - 30d AND "server" = '{server}'
        )
        """

        data = _influx_query_json(influx_url, influx_db, query, timeout=20)

        ports = []
        result = data.get("results", [{}])[0]
        if "series" in result:
            for row in result["series"][0]["values"]:
                ports.append(int(row[1]))

        return jsonify({"ok": True, "ports": sorted(ports)})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# BANDWIDTH SNMP TARGETS
# ============================================================
@report_bp.get("/api/bandwidth/snmp/targets")
@require_admin_api
def bandwidth_snmp_targets():
    try:
        influx_url = current_app.config["INFLUXDB_URL"]
        influx_db = current_app.config["INFLUXDB_DB"]

        q_hosts = """
        SELECT LAST("ifInOctets")
        FROM "interface"
        WHERE time >= now() - 30d
        GROUP BY "hostname","agent_host","template_type"
        """
        data = _influx_query_json(influx_url, influx_db, q_hosts, timeout=20)

        host_map = {}
        for series in data.get("results", [{}])[0].get("series", []):
            tags = series.get("tags", {})
            hostname = tags.get("hostname")
            if not hostname:
                continue

            host_map[hostname] = {
                "template_type": tags.get("template_type") or "",
                "agent_host": tags.get("agent_host"),
            }

        items = []
        templates = set()

        for hostname, meta in host_map.items():
            template_type = meta["template_type"]
            agent_host = meta["agent_host"]

            # 🔹 Decide interface tag based on template
            iface_tag = "ifName" if "fortigate" in template_type.lower() else "ifDescr"

            q_if = f"""
            SELECT LAST("ifInOctets")
            FROM "interface"
            WHERE time >= now() - 30d AND "hostname" = '{hostname}'
            GROUP BY "{iface_tag}"
            """

            r2 = _influx_query_json(influx_url, influx_db, q_if, timeout=20)

            interfaces = []
            for s in r2.get("results", [{}])[0].get("series", []):
                tag_val = s.get("tags", {}).get(iface_tag)
                if tag_val:
                    interfaces.append(tag_val)

            templates.add(template_type)

            items.append({
                "hostname": hostname,
                "agent_host": agent_host,
                "template_type": template_type,
                "interfaces": sorted(interfaces),
            })

        return jsonify({
            "ok": True,
            "templates": sorted(t for t in templates if t),
            "items": items,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# PING TARGETS
# ============================================================
@report_bp.get("/api/ping/targets")
@require_admin_api
def ping_targets():
    try:
        influx_url = current_app.config["INFLUXDB_URL"]
        influx_db = current_app.config["INFLUXDB_DB"]

        q = """
        SELECT LAST("average_response_ms")
        FROM ping
        WHERE time >= now() - 30d
        GROUP BY "url","friendly_name"
        """

        data = _influx_query_json(influx_url, influx_db, q, timeout=20)

        items = []
        for s in data.get("results", [{}])[0].get("series", []):
            tags = s.get("tags", {})
            url = tags.get("url")
            friendly = tags.get("friendly_name")

            if not url:
                continue

            label = f"{url} - {friendly}" if friendly else url
            items.append({"url": url, "friendly_name": friendly, "label": label})

        return jsonify({"ok": True, "targets": sorted(items, key=lambda x: x["label"].lower())})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# URL LIST
# ============================================================
@report_bp.get("/api/url/list")
@require_admin_api
def url_list():
    try:
        influx_url = current_app.config["INFLUXDB_URL"]
        influx_db = current_app.config["INFLUXDB_DB"]

        q = """
        SELECT LAST("response_time")
        FROM http_response
        WHERE time >= now() - 30d
        GROUP BY "server","friendly_name"
        """

        data = _influx_query_json(influx_url, influx_db, q, timeout=20)

        urls = []
        for s in data.get("results", [{}])[0].get("series", []):
            tags = s.get("tags", {})
            server = tags.get("server")
            friendly = tags.get("friendly_name")

            if not server:
                continue

            label = f"{server} - {friendly}" if friendly else server
            urls.append({"server": server, "friendly_name": friendly, "label": label})

        return jsonify({"ok": True, "urls": sorted(urls, key=lambda x: x["label"].lower())})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# DESKTOP CUSTOMERS
# ============================================================
@report_bp.get("/api/desktop/customers")
@require_admin_api
def desktop_customers():
    try:
        influx_url, influx_db = _desktop_influx_cfg()

        q = '''
        SELECT DISTINCT("customer_name")
        FROM (SELECT * FROM system WHERE time >= now() - 30d)
        '''

        data = _influx_query_json(influx_url, influx_db, q, timeout=20)

        customers = [row[1] for row in data.get("results", [{}])[0].get("series", [{}])[0].get("values", [])]

        return jsonify({"ok": True, "customers": sorted(customers)})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# DESKTOP INSTANCES
# ============================================================
@report_bp.get("/api/desktop/instances")
@require_admin_api
def desktop_instances():
    try:
        customer = request.args.get("customer", "ALL")

        influx_url, influx_db = _desktop_influx_cfg()

        if customer == "ALL":
            q = '''
            SELECT DISTINCT("host")
            FROM (SELECT * FROM system WHERE time >= now() - 30d)
            '''
        else:
            q = f'''
            SELECT DISTINCT("host")
            FROM (SELECT * FROM system 
                  WHERE time >= now() - 30d 
                  AND "customer_name" = '{customer}')
            '''

        data = _influx_query_json(influx_url, influx_db, q, timeout=20)

        hosts = [row[1] for row in data.get("results", [{}])[0].get("series", [{}])[0].get("values", [])]

        return jsonify({"ok": True, "hosts": sorted(hosts)})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ============================================================
# REPORT EXECUTION (ADMIN ONLY)
# ============================================================
@report_bp.post("/reports/run")
@require_admin_api
def reports_run():
    report_id = int(request.form.get("report_id"))
    from_ts = request.form.get("from")
    to_ts = request.form.get("to")
    fmt = request.form.get("format")
    instance = request.form.get("instance")
    customer = request.form.get("customer")

    # -----------------------------
    # Report 1001 - Server Availability
    # -----------------------------
    if report_id == 1001:
        outfile = ServerAvailabilityReport().run(
            instance=instance,
            start=from_ts,
            end=to_ts,
            customer=customer,
            fmt=fmt
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1002 - Server Performance
    # -----------------------------
    if report_id == 1002:
        outfile = ServerPerformanceReport().run(
            instance=instance,
            start=from_ts,
            end=to_ts,
            customer=customer,
            fmt=fmt
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1003 - Desktop Performance
    # -----------------------------
    if report_id == 1003:
        outfile = DesktopPerformanceReport().run(
            host=instance,
            start=from_ts,
            end=to_ts,
            customer=customer,
            fmt=fmt
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1004 - URL Performance
    # -----------------------------
    if report_id == 1004:
        outfile = UrlPerformanceReport().run(
            urls=request.form.getlist("instance"),
            start=from_ts,
            end=to_ts,
            fmt=fmt
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1005 - SNMP Bandwidth
    # -----------------------------
    if report_id == 1005:
        outfile = BandwidthUtilizationReport().run(
            template_type=request.form.get("template_type"),
            device=request.form.get("device_name"),
            interfaces=request.form.getlist("instance"),
            start=from_ts,
            end=to_ts,
            fmt=fmt
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1006 - Ping Performance
    # -----------------------------
    if report_id == 1006:
        outfile = PingPerformanceReport().run(
            urls=request.form.getlist("instance"),
            start=from_ts,
            end=to_ts,
            fmt=fmt,
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1007 - Port Performance
    # -----------------------------
    if report_id == 1007:
        outfile = PortPerformanceReport().run(
            targets=request.form.getlist("instance"),
            start=from_ts,
            end=to_ts,
            fmt=fmt,
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1008 - Fortigate VPN
    # -----------------------------
    if report_id == 1008:
        outfile = FortigateVpnReport().run(
            device=request.form.get("device_name"),
            start=from_ts,
            end=to_ts,
            fmt=fmt
        )
        return send_file(outfile, as_attachment=True)

    # -----------------------------
    # Report 1009 - Fortigate SD-WAN
    # -----------------------------
    if report_id == 1009:
        outfile = FortigateSdwanReport().run(
            device=request.form.get("device_name"),
            start=from_ts,
            end=to_ts,
            fmt=fmt
        )
        return send_file(outfile, as_attachment=True)

    return jsonify({"ok": False, "error": "Unknown report"}), 400

