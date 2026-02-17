from datetime import datetime, timezone
import re

from flask import Blueprint, jsonify, request, send_file

from extensions import db
from models.alert_rule import AlertRule
from models.alert_rule_state import AlertRuleState
from models.copilot_audit import CopilotAuditLog
from models.device_status_alert import DeviceStatusAlert
from models.itam import ItamAsset, ItamAssetItomBinding, ItamAssetSource
from models.itom import BusinessApplication
from models.link_monitor import LinkMonitor
from models.ping import PingConfig
from models.port_monitor import PortMonitor
from models.proxy import ProxyServer
from models.snmp import SnmpConfig
from models.url_monitor import UrlMonitor
from reports.desktop.rpt_1003 import DesktopPerformanceReport
from reports.fortigate.rpt_1008 import FortigateVpnReport
from reports.fortigate.rpt_1009 import FortigateSdwanReport
from reports.ping.rpt_1006 import PingPerformanceReport
from reports.port.rpt_1007 import PortPerformanceReport
from reports.server.rpt_1001 import ServerAvailabilityReport
from reports.server.rpt_1002 import ServerPerformanceReport
from reports.snmp.rpt_1005 import BandwidthUtilizationReport
from reports.url.rpt_1004 import UrlPerformanceReport
from services.itam.risk import build_risk_report
from services.ops_cache import cached
import security

copilot_bp = Blueprint("copilot", __name__)


REPORT_CATALOG = [
    {"id": 1001, "name": "Server Availability", "keywords": ["server availability", "availability", "uptime"], "required": []},
    {"id": 1002, "name": "Server Performance", "keywords": ["server performance", "cpu", "memory", "disk performance"], "required": []},
    {"id": 1003, "name": "Desktop Performance", "keywords": ["desktop performance", "desktop"], "required": ["instance"]},
    {"id": 1004, "name": "URL Performance", "keywords": ["url report", "website report", "http report"], "required": ["instance"]},
    {"id": 1005, "name": "SNMP Bandwidth Utilization", "keywords": ["bandwidth report", "snmp bandwidth", "interface utilization"], "required": ["template_type", "device_name", "instance"]},
    {"id": 1006, "name": "Ping Performance", "keywords": ["ping report", "latency report"], "required": ["instance"]},
    {"id": 1007, "name": "Port Performance", "keywords": ["port report", "port performance"], "required": ["instance"]},
    {"id": 1008, "name": "Fortigate VPN", "keywords": ["fortigate vpn", "vpn report"], "required": ["device_name"]},
    {"id": 1009, "name": "Fortigate SD-WAN", "keywords": ["sdwan report", "fortigate sdwan"], "required": ["device_name"]},
]


def _allowed_customer_id():
    user = security.get_current_user()
    return security.get_allowed_customer_id(user)


def _current_user():
    return security.get_current_user()


def _is_admin():
    u = _current_user()
    return bool(u and u.is_admin)


def _can(permission_code):
    u = _current_user()
    if not u:
        return False
    return security.has_permission(u, permission_code)


def _audit(action, status="ok", query_text=None, details=None):
    u = _current_user()
    row = CopilotAuditLog(
        user_id=u.id if u else None,
        username=u.username if u else None,
        customer_id=u.customer_id if u else None,
        action=action,
        status=status,
        query_text=query_text,
        details_json=details or {},
    )
    db.session.add(row)
    db.session.commit()


def _scope_query(query, model_cls):
    allowed = _allowed_customer_id()
    if allowed is None:
        return query
    if hasattr(model_cls, "customer_id"):
        return query.filter(getattr(model_cls, "customer_id") == allowed)
    return query


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _date_utc():
    return datetime.now(timezone.utc).date()


def _parse_report_time_window(query_text):
    q = (query_text or "").lower()
    today = _date_utc()

    if "yesterday" in q:
        d = today.fromordinal(today.toordinal() - 1)
        return d.isoformat(), d.isoformat()
    if "today" in q:
        return today.isoformat(), today.isoformat()
    if "last 24" in q:
        d = today.fromordinal(today.toordinal() - 1)
        return d.isoformat(), today.isoformat()

    m = re.search(r"last\s+(\d+)\s+day", q)
    if m:
        days = max(1, min(int(m.group(1)), 90))
        start = today.fromordinal(today.toordinal() - days)
        return start.isoformat(), today.isoformat()

    if "last week" in q or "last 7 day" in q or "weekly" in q:
        start = today.fromordinal(today.toordinal() - 7)
        return start.isoformat(), today.isoformat()
    if "last month" in q:
        start = today.fromordinal(today.toordinal() - 30)
        return start.isoformat(), today.isoformat()

    # default reasonable range
    start = today.fromordinal(today.toordinal() - 7)
    return start.isoformat(), today.isoformat()


def _parse_report_format(query_text):
    q = (query_text or "").lower()
    if "excel" in q or "xlsx" in q:
        return "excel"
    return "pdf"


def _detect_report(query_text):
    q = (query_text or "").lower()
    mid = re.search(r"\b(100[1-9])\b", q)
    if mid:
        rid = int(mid.group(1))
        return next((r for r in REPORT_CATALOG if r["id"] == rid), None)

    for r in REPORT_CATALOG:
        for k in r["keywords"]:
            if k in q:
                return r
    return None


def _extract_named_value(query_text, key):
    q = (query_text or "")
    pattern = rf"{key}\s*[:=]\s*([^\s,;]+)"
    m = re.search(pattern, q, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _build_report_intent(query_text):
    report = _detect_report(query_text)
    if not report:
        return None

    run_asked = any(k in (query_text or "").lower() for k in ("run", "generate", "download", "export"))
    fmt = _parse_report_format(query_text)
    from_dt, to_dt = _parse_report_time_window(query_text)

    params = {
        "report_id": report["id"],
        "format": fmt,
        "from": from_dt,
        "to": to_dt,
        "instance": _extract_named_value(query_text, "instance"),
        "customer": _extract_named_value(query_text, "customer"),
        "device_name": _extract_named_value(query_text, "device"),
        "template_type": _extract_named_value(query_text, "template"),
    }

    required = report["required"]
    missing = [x for x in required if not params.get(x)]

    return {
        "report_id": report["id"],
        "report_name": report["name"],
        "execution_requested": run_asked,
        "execution_ready": len(missing) == 0,
        "format": fmt,
        "from": from_dt,
        "to": to_dt,
        "required_fields": required,
        "missing_fields": missing,
        "params": params,
        "run_endpoint": "/api/copilot/report/run",
        "legacy_run_endpoint": "/reports/run",
        "open_url": "/report_config",
    }


def _required_fields_for_report(report_id):
    row = next((r for r in REPORT_CATALOG if r["id"] == int(report_id)), None)
    return row["required"] if row else []


def _report_error(message, code=400, details=None):
    return jsonify({"ok": False, "error": message, "details": details or {}}), code


def _run_report(report_id, from_ts, to_ts, fmt, instance=None, customer=None, template_type=None, device_name=None, instance_list=None):
    rid = int(report_id)
    if rid == 1001:
        return ServerAvailabilityReport().run(instance=instance, start=from_ts, end=to_ts, customer=customer, fmt=fmt)
    if rid == 1002:
        return ServerPerformanceReport().run(instance=instance, start=from_ts, end=to_ts, customer=customer, fmt=fmt)
    if rid == 1003:
        return DesktopPerformanceReport().run(host=instance, start=from_ts, end=to_ts, customer=customer, fmt=fmt)
    if rid == 1004:
        urls = instance_list or ([instance] if instance else [])
        return UrlPerformanceReport().run(urls=urls, start=from_ts, end=to_ts, fmt=fmt)
    if rid == 1005:
        interfaces = instance_list or ([instance] if instance else [])
        return BandwidthUtilizationReport().run(template_type=template_type, device=device_name, interfaces=interfaces, start=from_ts, end=to_ts, fmt=fmt)
    if rid == 1006:
        urls = instance_list or ([instance] if instance else [])
        return PingPerformanceReport().run(urls=urls, start=from_ts, end=to_ts, fmt=fmt)
    if rid == 1007:
        targets = instance_list or ([instance] if instance else [])
        return PortPerformanceReport().run(targets=targets, start=from_ts, end=to_ts, fmt=fmt)
    if rid == 1008:
        return FortigateVpnReport().run(device=device_name, start=from_ts, end=to_ts, fmt=fmt)
    if rid == 1009:
        return FortigateSdwanReport().run(device=device_name, start=from_ts, end=to_ts, fmt=fmt)
    return None


def _tool_kpi_summary():
    cid = _allowed_customer_id()
    key = f"copilot:kpi:{cid}"

    def _build():
        ping = _scope_query(PingConfig.query, PingConfig).count()
        port = _scope_query(PortMonitor.query, PortMonitor).count()
        url = _scope_query(UrlMonitor.query, UrlMonitor).count()
        snmp = _scope_query(SnmpConfig.query, SnmpConfig).count()
        link = _scope_query(LinkMonitor.query, LinkMonitor).count()

        proxy_q = ProxyServer.query
        if cid is not None and hasattr(ProxyServer, "customer_id"):
            proxy_q = proxy_q.filter(ProxyServer.customer_id == cid)
        proxy_total = proxy_q.count()
        proxy_active = proxy_q.filter(ProxyServer.last_heartbeat.isnot(None)).count()

        active_device_alerts_q = DeviceStatusAlert.query.filter(
            DeviceStatusAlert.is_active.is_(True),
            DeviceStatusAlert.last_status == "DOWN",
        )
        if cid is not None and hasattr(DeviceStatusAlert, "customer_id"):
            active_device_alerts_q = active_device_alerts_q.filter(
                DeviceStatusAlert.customer_id == cid
            )
        active_device_alerts = active_device_alerts_q.count()

        active_rule_states_q = (
            db.session.query(AlertRuleState)
            .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
            .filter(AlertRuleState.is_active.is_(True))
        )
        if cid is not None:
            active_rule_states_q = active_rule_states_q.filter(
                AlertRule.customer_id == cid
            )
        active_rule_states = active_rule_states_q.count()

        return {
            "timestamp_utc": _now_iso(),
            "monitor_totals": {
                "ping": ping,
                "port": port,
                "url": url,
                "snmp": snmp,
                "link": link,
            },
            "proxy": {"total": proxy_total, "active": proxy_active},
            "active_issues": {
                "device_down_alerts": active_device_alerts,
                "rule_triggered_alerts": active_rule_states,
                "total": active_device_alerts + active_rule_states,
            },
        }

    return cached(key, 30, _build)


def _tool_recent_critical(limit=10):
    limit = max(1, min(int(limit or 10), 50))
    cid = _allowed_customer_id()
    key = f"copilot:critical:{cid}:{limit}"

    def _build():
        items = []

        dq = DeviceStatusAlert.query.filter(
            DeviceStatusAlert.is_active.is_(True),
            DeviceStatusAlert.last_status == "DOWN",
        ).order_by(DeviceStatusAlert.updated_at.desc())
        if cid is not None and hasattr(DeviceStatusAlert, "customer_id"):
            dq = dq.filter(DeviceStatusAlert.customer_id == cid)
        for a in dq.limit(limit).all():
            items.append(
                {
                    "kind": "device",
                    "source": a.source,
                    "target": a.device,
                    "state": a.last_status,
                    "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                }
            )

        rq = (
            db.session.query(AlertRuleState, AlertRule)
            .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
            .filter(AlertRuleState.is_active.is_(True))
            .order_by(AlertRuleState.updated_at.desc())
        )
        if cid is not None:
            rq = rq.filter(AlertRule.customer_id == cid)
        for s, r in rq.limit(limit).all():
            items.append(
                {
                    "kind": "rule",
                    "rule_id": r.id,
                    "rule_name": r.name,
                    "monitoring_type": r.monitoring_type,
                    "target": s.target_value,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
            )

        items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return {"timestamp_utc": _now_iso(), "items": items[:limit]}

    return cached(key, 20, _build)


def _tool_list_app_health(limit=8):
    q = _scope_query(BusinessApplication.query, BusinessApplication).order_by(
        BusinessApplication.name.asc()
    )
    rows = q.limit(max(1, min(limit, 50))).all()
    items = []
    for app in rows:
        services = app.services or []
        items.append(
            {
                "app_id": app.id,
                "name": app.name,
                "services": len(services),
            }
        )
    return {"timestamp_utc": _now_iso(), "items": items}


_ITAM_MONITORING_SOURCE_HINTS = {"servers_cache", "desktop_cache", "snmp"}


def _tool_itam_summary():
    cid = _allowed_customer_id()
    key = f"copilot:itam:summary:{cid}"

    def _build():
        try:
            q = _scope_query(ItamAsset.query, ItamAsset)
            rows = q.all()
        except Exception:
            return {
                "timestamp_utc": _now_iso(),
                "total_assets": 0,
                "by_type": {},
                "by_status": {},
            }

        by_type = {}
        by_status = {}
        for row in rows:
            atype = (row.asset_type or "unknown").strip().lower() or "unknown"
            status = (row.status or "unknown").strip().lower() or "unknown"
            by_type[atype] = by_type.get(atype, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "timestamp_utc": _now_iso(),
            "total_assets": len(rows),
            "by_type": by_type,
            "by_status": by_status,
        }

    return cached(key, 30, _build)


def _tool_itam_coverage():
    cid = _allowed_customer_id()
    key = f"copilot:itam:coverage:{cid}"

    def _build():
        try:
            aq = _scope_query(ItamAsset.query, ItamAsset)
            assets = aq.all()
        except Exception:
            return {
                "timestamp_utc": _now_iso(),
                "total_assets": 0,
                "monitoring_covered_assets": 0,
                "monitoring_gap_assets": 0,
                "monitoring_coverage_pct": 0.0,
                "itom_bound_assets": 0,
                "itom_unbound_assets": 0,
                "itom_binding_pct": 0.0,
            }
        asset_ids = [x.id for x in assets]
        total = len(assets)
        if not asset_ids:
            return {
                "timestamp_utc": _now_iso(),
                "total_assets": 0,
                "monitoring_covered_assets": 0,
                "monitoring_gap_assets": 0,
                "monitoring_coverage_pct": 0.0,
                "itom_bound_assets": 0,
                "itom_unbound_assets": 0,
                "itom_binding_pct": 0.0,
            }

        sq = _scope_query(ItamAssetSource.query, ItamAssetSource).filter(
            ItamAssetSource.asset_id.in_(asset_ids)
        )
        src_rows = sq.with_entities(ItamAssetSource.asset_id, ItamAssetSource.source_name).all()
        source_map = {}
        for aid, source_name in src_rows:
            source_map.setdefault(aid, set()).add((source_name or "").strip().lower())

        bq = _scope_query(ItamAssetItomBinding.query, ItamAssetItomBinding).filter(
            ItamAssetItomBinding.asset_id.in_(asset_ids)
        )
        bound_ids = {
            x[0]
            for x in bq.with_entities(ItamAssetItomBinding.asset_id).distinct().all()
        }

        monitored = 0
        bound = 0
        for asset in assets:
            if source_map.get(asset.id, set()) & _ITAM_MONITORING_SOURCE_HINTS:
                monitored += 1
            if asset.id in bound_ids:
                bound += 1

        return {
            "timestamp_utc": _now_iso(),
            "total_assets": total,
            "monitoring_covered_assets": monitored,
            "monitoring_gap_assets": max(0, total - monitored),
            "monitoring_coverage_pct": round((monitored * 100.0 / total), 2) if total else 0.0,
            "itom_bound_assets": bound,
            "itom_unbound_assets": max(0, total - bound),
            "itom_binding_pct": round((bound * 100.0 / total), 2) if total else 0.0,
        }

    return cached(key, 30, _build)


def _tool_itam_risk():
    cid = _allowed_customer_id()
    key = f"copilot:itam:risk:{cid}"

    def _build():
        try:
            report = build_risk_report(customer_id=cid, limit_assets=800, stale_days=14)
        except Exception:
            report = {"summary": {}, "top_risks": [], "drift_alerts": []}
        summary = report.get("summary") or {}
        top = report.get("top_risks") or []
        drift = report.get("drift_alerts") or []
        return {
            "timestamp_utc": _now_iso(),
            "summary": summary,
            "top_risks": top[:5],
            "drift_alerts": drift[:5],
        }

    return cached(key, 30, _build)


def _find_app_by_query(query_text):
    q = (query_text or "").strip().lower()
    if not q:
        return None

    apps = _scope_query(BusinessApplication.query, BusinessApplication).all()
    # exact/contains match on app name
    for app in apps:
        n = (app.name or "").lower()
        if n and (n in q or q in n):
            return app
    return None


def _draft_actions(
    query_text,
    critical_items,
    report_intent=None,
    itam_summary=None,
    itam_coverage=None,
    itam_risk=None,
):
    q = (query_text or "").lower()
    actions = []

    if any(k in q for k in ("remediate", "fix", "runbook")):
        actions.append(
            "Use runbook mode: ask 'suggest remediation for top 3 critical alerts'."
        )
    if any(k in q for k in ("application", "impact", "service")):
        actions.append(
            "Open ITOM Dashboard and select the application node to inspect impact chain."
        )
    if report_intent:
        if report_intent["missing_fields"]:
            actions.append(
                "Provide missing report fields as key:value (example: instance:web01 device:FGT-01)."
            )
        else:
            actions.append(
                "Open Monitoring Reports and run with prepared report_id/from/to/format."
            )
    if critical_items:
        actions.append(
            "Prioritize DOWN alerts first, then IMPACTED/DEGRADED services."
        )

    if itam_summary:
        actions.append("Open ITAM Inventory for unified IT/OT/Cloud asset drill-down.")
    if itam_coverage:
        gap = int((itam_coverage or {}).get("monitoring_gap_assets") or 0)
        if gap > 0:
            actions.append("Use ITAM Coverage Gaps to onboard monitors for unmonitored assets.")
    if itam_risk:
        high = int(((itam_risk or {}).get("summary") or {}).get("high_risk_assets") or 0)
        if high > 0:
            actions.append("Review Top Risk Assets and Drift Alerts, then fix lifecycle/compliance gaps first.")

    if not actions:
        actions.append("Ask for 'overall summary', 'critical alerts', or 'application impact'.")
    return actions


def _build_ui_actions(
    report_intent=None,
    app=None,
    use_itam=False,
    itam_coverage=None,
    itam_risk=None,
):
    items = []

    if report_intent:
        items.append({"label": "Open Reports", "url": report_intent.get("open_url") or "/report_config"})

    if app is not None:
        items.append({"label": "Open ITOM Dashboard", "url": "/itom/applications"})

    if use_itam or itam_coverage or itam_risk:
        items.append({"label": "Open ITAM Inventory", "url": "/itam/assets"})

    if use_itam or itam_coverage:
        items.append({"label": "Open ITAM Coverage Gaps", "url": "/itam/assets#itamCoverageGapsSection"})

    if use_itam or itam_risk:
        items.append({"label": "Open ITAM Risk & Drift", "url": "/itam/assets#itamRiskDriftSection"})

    out = []
    seen = set()
    for row in items:
        label = (row.get("label") or "").strip()
        url = (row.get("url") or "").strip()
        if not label or not url:
            continue
        mark = (label, url)
        if mark in seen:
            continue
        seen.add(mark)
        out.append({"label": label, "url": url})
    return out[:5]


def _build_response(query_text):
    q = (query_text or "").strip()
    lq = q.lower()
    evidence = []

    use_summary = any(k in lq for k in ("summary", "overview", "status", "health"))
    use_alerts = any(k in lq for k in ("critical", "alert", "incident", "down"))
    use_apps = any(k in lq for k in ("application", "itom", "service", "impact", "dependency"))
    use_reports = any(k in lq for k in ("report", "pdf", "excel", "download", "export"))
    use_itam = any(
        k in lq
        for k in (
            "itam",
            "asset",
            "inventory",
            "cmdb",
            "cloud asset",
            "ot asset",
            "lifecycle",
            "dedup",
            "reconcile",
            "drift",
            "risk",
            "coverage gap",
        )
    )

    if not (use_summary or use_alerts or use_apps or use_reports or use_itam):
        use_summary = True
        use_alerts = True

    summary = _tool_kpi_summary() if use_summary else None
    recent = _tool_recent_critical(10) if use_alerts else {"items": []}
    report_intent = _build_report_intent(q) if use_reports else None

    app = _find_app_by_query(lq) if use_apps else None
    apps = _tool_list_app_health(8) if use_apps and not app else None
    itam_summary = _tool_itam_summary() if (use_itam or use_summary) else None
    itam_coverage = _tool_itam_coverage() if (use_itam or use_summary) else None
    itam_risk = _tool_itam_risk() if use_itam else None

    if summary:
        evidence.append({"tool": "kpi_summary", "result": summary})
    if recent.get("items"):
        evidence.append({"tool": "recent_critical", "result": recent})
    if app:
        evidence.append(
            {
                "tool": "application_match",
                "result": {"app_id": app.id, "name": app.name, "services": len(app.services or [])},
            }
        )
    elif apps:
        evidence.append({"tool": "application_list", "result": apps})
    if report_intent:
        evidence.append({"tool": "report_intent", "result": report_intent})
    if itam_summary:
        evidence.append({"tool": "itam_inventory_summary", "result": itam_summary})
    if itam_coverage:
        evidence.append({"tool": "itam_coverage_summary", "result": itam_coverage})
    if itam_risk:
        evidence.append({"tool": "itam_risk_summary", "result": itam_risk})

    lines = []
    if summary:
        mt = summary["monitor_totals"]
        lines.append(
            "Current scope: "
            f"Ping {mt['ping']}, Port {mt['port']}, URL {mt['url']}, SNMP {mt['snmp']}, Link {mt['link']}."
        )
        lines.append(
            "Active issues: "
            f"{summary['active_issues']['total']} "
            f"({summary['active_issues']['device_down_alerts']} device-down + "
            f"{summary['active_issues']['rule_triggered_alerts']} rule-triggered)."
        )
    if recent.get("items"):
        top = recent["items"][:3]
        lines.append(
            "Top critical: "
            + "; ".join(
                [
                    (f"{x.get('source','rule')}:{x.get('target')}" if x["kind"] == "device"
                     else f"rule:{x.get('rule_name')} -> {x.get('target')}")
                    for x in top
                ]
            )
            + "."
        )
    if app:
        lines.append(
            f"Matched application: {app.name} (id {app.id}) with {len(app.services or [])} services."
        )
    elif apps and apps.get("items"):
        names = ", ".join([x["name"] for x in apps["items"][:5]])
        lines.append(f"Available applications: {names}.")
    if report_intent:
        lines.append(
            f"Report match: {report_intent['report_name']} (ID {report_intent['report_id']}) "
            f"from {report_intent['from']} to {report_intent['to']} as {report_intent['format']}."
        )
        if report_intent["missing_fields"]:
            lines.append(
                "Missing fields: " + ", ".join(report_intent["missing_fields"]) + "."
            )
        elif report_intent["execution_requested"]:
            lines.append("Report request is ready to run from Monitoring Reports.")

    if itam_summary:
        t = int(itam_summary.get("total_assets") or 0)
        by_type = itam_summary.get("by_type") or {}
        lines.append(
            "ITAM inventory: "
            f"{t} assets (servers {int(by_type.get('server') or 0)}, "
            f"workstations {int(by_type.get('workstation') or 0)}, "
            f"network {int(by_type.get('network_device') or 0)}, "
            f"cloud {int(by_type.get('cloud_asset') or 0)}, "
            f"OT {int(by_type.get('ot_device') or 0)})."
        )
    if itam_coverage:
        lines.append(
            "ITAM coverage: "
            f"{itam_coverage.get('monitoring_coverage_pct', 0)}% monitored, "
            f"{itam_coverage.get('itom_binding_pct', 0)}% ITOM-bound, "
            f"gaps {int(itam_coverage.get('monitoring_gap_assets') or 0)}."
        )
    if itam_risk:
        rs = itam_risk.get("summary") or {}
        top = itam_risk.get("top_risks") or []
        lines.append(
            "ITAM risk posture: "
            f"avg quality {rs.get('avg_quality_score', 0)}, "
            f"high risk {int(rs.get('high_risk_assets') or 0)}, "
            f"drift alerts {int(rs.get('drift_alert_assets') or 0)}."
        )
        if top:
            lines.append(
                "Top risk assets: "
                + "; ".join(
                    [
                        f"{x.get('asset_name')} ({int(x.get('risk_score') or 0)})"
                        for x in top[:3]
                    ]
                )
                + "."
            )

    actions = _draft_actions(
        q,
        recent.get("items", []),
        report_intent=report_intent,
        itam_summary=itam_summary,
        itam_coverage=itam_coverage,
        itam_risk=itam_risk,
    )
    ui_actions = _build_ui_actions(
        report_intent=report_intent,
        app=app,
        use_itam=use_itam,
        itam_coverage=itam_coverage,
        itam_risk=itam_risk,
    )

    return {
        "answer": " ".join(lines) if lines else "No operational data found for current scope.",
        "evidence": evidence,
        "actions": actions,
        "ui_actions": ui_actions,
        "report_intent": report_intent,
    }


@copilot_bp.get("/api/copilot/suggestions")
@security.login_required_api
def copilot_suggestions():
    if not _can("copilot.use"):
        return jsonify({"ok": False, "error": "Forbidden: Missing permission 'copilot.use'"}), 403
    return jsonify(
        {
            "ok": True,
            "items": [
                "Give me an overall NOC summary.",
                "Show current critical alerts.",
                "Which services are impacted right now?",
                "Give me an ITAM inventory and coverage summary.",
                "Show top ITAM risk and drift assets.",
                "What are the biggest ITAM monitoring gaps?",
                "Generate Server Availability report for last 7 days in PDF.",
                "Run report 1008 for device:FGT-01 from yesterday to today.",
                "What should I prioritize in the next 15 minutes?",
                "Find impact for application <name>.",
            ],
        }
    )


@copilot_bp.post("/api/copilot/query")
@security.login_required_api
def copilot_query():
    if not _can("copilot.use"):
        _audit("copilot.query", status="forbidden", details={"reason": "missing_permission"})
        return jsonify({"ok": False, "error": "Forbidden: Missing permission 'copilot.use'"}), 403

    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "query is required"}), 400

    data = _build_response(query)
    _audit("copilot.query", query_text=query, details={"report_intent": data.get("report_intent")})
    return jsonify({"ok": True, **data})


@copilot_bp.post("/api/copilot/report/run")
@security.login_required_api
def copilot_report_run():
    if not _can("copilot.run_reports"):
        _audit("copilot.report.run", status="forbidden", details={"reason": "missing_permission"})
        return _report_error("Forbidden: Missing permission 'copilot.run_reports'", 403)

    data = request.form if request.form else (request.get_json(silent=True) or {})
    try:
        report_id = int(data.get("report_id"))
    except Exception:
        _audit("copilot.report.run", status="error", details={"reason": "invalid_report_id"})
        return _report_error("Invalid or missing report_id", 400)

    from_ts = (data.get("from") or "").strip()
    to_ts = (data.get("to") or "").strip()
    fmt = (data.get("format") or "pdf").strip().lower()
    if fmt not in ("pdf", "excel"):
        return _report_error("format must be 'pdf' or 'excel'", 400)

    if not from_ts or not to_ts:
        return _report_error("from and to are required (YYYY-MM-DD)", 400)

    instance_list = []
    if hasattr(data, "getlist"):
        instance_list = [x for x in data.getlist("instance") if str(x).strip()]
    elif isinstance(data.get("instance"), list):
        instance_list = [x for x in data.get("instance") if str(x).strip()]
    elif data.get("instance"):
        instance_list = [str(data.get("instance")).strip()]

    params = {
        "instance": (data.get("instance") or "").strip() if not isinstance(data.get("instance"), list) else "",
        "customer": (data.get("customer") or "").strip(),
        "template_type": (data.get("template_type") or "").strip(),
        "device_name": (data.get("device_name") or "").strip(),
    }

    missing = []
    for field in _required_fields_for_report(report_id):
        if field == "instance":
            if not instance_list and not params.get("instance"):
                missing.append(field)
        elif not params.get(field):
            missing.append(field)
    if missing:
        return _report_error("Missing required fields", 400, {"missing_fields": missing})

    try:
        outfile = _run_report(
            report_id=report_id,
            from_ts=from_ts,
            to_ts=to_ts,
            fmt=fmt,
            instance=params.get("instance"),
            customer=params.get("customer"),
            template_type=params.get("template_type"),
            device_name=params.get("device_name"),
            instance_list=instance_list,
        )
        if not outfile:
            _audit("copilot.report.run", status="error", details={"reason": "unsupported_report", "report_id": report_id})
            return _report_error("Unsupported report_id", 400)

        _audit(
            "copilot.report.run",
            query_text=f"report_id={report_id}",
            details={
                "report_id": report_id,
                "from": from_ts,
                "to": to_ts,
                "format": fmt,
                "instance": params.get("instance"),
                "customer": params.get("customer"),
                "template_type": params.get("template_type"),
                "device_name": params.get("device_name"),
                "instance_list": instance_list,
            },
        )
        return send_file(outfile, as_attachment=True)
    except Exception as e:
        _audit("copilot.report.run", status="error", details={"reason": "exception", "message": str(e), "report_id": report_id})
        return _report_error("Report execution failed", 500, {"message": str(e)})


@copilot_bp.get("/api/copilot/audit")
@security.login_required_api
def copilot_audit_list():
    if not _can("view_admin"):
        return jsonify({"ok": False, "error": "Forbidden: Missing permission 'view_admin'"}), 403

    limit = max(1, min(request.args.get("limit", 100, type=int), 500))
    q = CopilotAuditLog.query.order_by(CopilotAuditLog.created_at.desc())
    cid = _allowed_customer_id()
    if cid is not None:
        q = q.filter(CopilotAuditLog.customer_id == cid)
    rows = q.limit(limit).all()
    return jsonify({"ok": True, "items": [x.to_dict() for x in rows]})
