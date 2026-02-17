from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy.orm import joinedload

from extensions import db
from models.alert_rule import AlertRule
from models.alert_rule_state import AlertRuleState
from models.customer import Customer
from models.device_status_alert import DeviceStatusAlert
from models.idrac import IdracConfig
from models.itom import (
    ApplicationService,
    BusinessApplication,
    ServiceBinding,
    ServiceDependency,
)
from models.itom_layout import ItomGraphLayout
from models.ilo import IloConfig
from models.link_monitor import LinkMonitor
from models.oracle_db_monitor import OracleDbMonitor
from models.ping import PingConfig
from models.port_monitor import PortMonitor
from models.proxy import ProxyServer
from models.snmp import SnmpConfig
from models.sqlserver_monitor import SqlServerMonitor
from models.url_monitor import UrlMonitor
from services.ops_cache import cached
import security

itom_bp = Blueprint("itom", __name__)


def _normalize_key(value):
    return str(value or "").strip().lower()


def _allowed_customer_id():
    user = security.get_current_user()
    return security.get_allowed_customer_id(user)


def _current_user():
    return security.get_current_user()


def _is_admin():
    user = security.get_current_user()
    return bool(user and user.is_admin and user.customer_id is None)


def _scope_query(query, model_cls):
    allowed = _allowed_customer_id()
    if allowed is None:
        return query
    return query.filter(getattr(model_cls, "customer_id") == allowed)


def _effective_customer_id(payload_customer_id):
    allowed = _allowed_customer_id()
    if allowed is None:
        return payload_customer_id
    return allowed


def _service_belongs_to_scope(service):
    allowed = _allowed_customer_id()
    if allowed is None:
        return True
    return service.customer_id == allowed


MONITOR_TYPES = (
    "server",
    "desktop",
    "ping",
    "url",
    "port",
    "snmp",
    "link",
    "sqlserver",
    "oracle",
    "idrac",
    "ilo",
    "proxy",
)


def _split_ports(ports):
    out = []
    for part in str(ports or "").split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out


def _device_rows_for_source(customer_id, source_like):
    q = DeviceStatusAlert.query.filter(
        DeviceStatusAlert.source.ilike(f"%{source_like}%")
    )
    if hasattr(DeviceStatusAlert, "customer_id"):
        q = q.filter(DeviceStatusAlert.customer_id == customer_id)
    rows = (
        q.with_entities(DeviceStatusAlert.device)
        .distinct()
        .order_by(DeviceStatusAlert.device.asc())
        .all()
    )
    return [r[0] for r in rows if r and r[0]]


def _monitor_options_for(customer_id, monitor_type):
    mtype = (monitor_type or "").strip().lower()

    if mtype == "ping":
        rows = (
            PingConfig.query.filter(PingConfig.customer_id == customer_id)
            .order_by(PingConfig.name.asc())
            .all()
        )
        return [
            {"value": x.host, "label": f"{x.name} ({x.host})"}
            for x in rows
        ]

    if mtype == "url":
        rows = (
            UrlMonitor.query.filter(UrlMonitor.customer_id == customer_id)
            .order_by(UrlMonitor.name.asc())
            .all()
        )
        return [
            {"value": x.url, "label": f"{x.name or x.url} ({x.url})"}
            for x in rows
        ]

    if mtype == "port":
        rows = (
            PortMonitor.query.filter(PortMonitor.customer_id == customer_id)
            .order_by(PortMonitor.friendly_name.asc())
            .all()
        )
        out = []
        for x in rows:
            for p in _split_ports(x.ports):
                val = f"{x.host_ip}:{p}"
                label = f"{x.friendly_name or x.host_ip}:{p}"
                out.append({"value": val, "label": label})
        return out

    if mtype == "snmp":
        rows = (
            SnmpConfig.query.filter(SnmpConfig.customer_id == customer_id)
            .order_by(SnmpConfig.name.asc())
            .all()
        )
        return [
            {"value": x.device_ip, "label": f"{x.name} ({x.device_ip})"}
            for x in rows
        ]

    if mtype == "link":
        rows = (
            LinkMonitor.query.filter(LinkMonitor.customer_id == customer_id)
            .order_by(LinkMonitor.link_name.asc())
            .all()
        )
        return [
            {
                "value": f"{x.ip_address}:{x.if_index}",
                "label": f"{x.link_name} ({x.ip_address} if:{x.if_index})",
            }
            for x in rows
        ]

    if mtype == "sqlserver":
        rows = (
            SqlServerMonitor.query.filter(SqlServerMonitor.customer_id == customer_id)
            .order_by(SqlServerMonitor.friendly_name.asc())
            .all()
        )
        return [
            {
                "value": f"{x.ip_address}:{x.port}",
                "label": f"{x.friendly_name} ({x.ip_address}:{x.port})",
            }
            for x in rows
        ]

    if mtype == "oracle":
        rows = (
            OracleDbMonitor.query.filter(OracleDbMonitor.customer_id == customer_id)
            .order_by(OracleDbMonitor.friendly_name.asc())
            .all()
        )
        return [
            {
                "value": f"oracle:{x.id}:{x.service_name}:__ALL__",
                "label": f"{x.friendly_name or x.service_name} ({x.host}:{x.port}/{x.service_name})",
            }
            for x in rows
        ]

    if mtype == "idrac":
        rows = (
            IdracConfig.query.filter(IdracConfig.customer_id == customer_id)
            .order_by(IdracConfig.device_ip.asc())
            .all()
        )
        return [
            {"value": x.device_ip, "label": f"{x.device_ip}"}
            for x in rows
        ]

    if mtype == "ilo":
        rows = (
            IloConfig.query.filter(IloConfig.customer_id == customer_id)
            .order_by(IloConfig.device_ip.asc())
            .all()
        )
        return [
            {"value": x.device_ip, "label": f"{x.device_ip}"}
            for x in rows
        ]

    if mtype == "server":
        return [
            {"value": x, "label": x}
            for x in _device_rows_for_source(customer_id, "server")
        ]

    if mtype == "desktop":
        return [
            {"value": x, "label": x}
            for x in _device_rows_for_source(customer_id, "desktop")
        ]

    if mtype == "proxy":
        rows = ProxyServer.query.order_by(ProxyServer.ip_address.asc()).all()
        return [
            {"value": x.ip_address, "label": f"{x.ip_address}"}
            for x in rows
        ]

    return []


def _binding_is_active(binding, active_keys):
    ref = _normalize_key(binding.monitor_ref)
    mtype = _normalize_key(binding.monitor_type)
    if not ref:
        return False

    candidates = {
        ref,
        f"{mtype}:{ref}",
    }
    if candidates & active_keys:
        return True

    # Host-level bindings should match derived host targets (disk/net) too.
    for k in active_keys:
        if k.startswith(f"{ref}|") or k.startswith(f"{mtype}:{ref}|"):
            return True

    # Oracle binding may be stored as oracle:<id>:<db>:__ALL__ while active state is per tablespace.
    if mtype == "oracle" and ref.endswith(":__all__"):
        prefix = ref.rsplit(":", 1)[0] + ":"
        for k in active_keys:
            if k.startswith(prefix):
                return True

    return False


def _normalize_monitor_type(raw):
    m = _normalize_key(raw).replace(" ", "_")
    mapped = {
        "snmp_interface": "snmp",
        "service_down": "server",
        "bandwidth": "link",
        "server": "server",
        "desktop": "desktop",
        "ping": "ping",
        "port": "port",
        "url": "url",
        "snmp": "snmp",
        "link": "link",
        "sqlserver": "sqlserver",
        "oracle": "oracle",
        "idrac": "idrac",
        "ilo": "ilo",
        "proxy": "proxy",
    }
    v = mapped.get(m, m)
    return v if v in MONITOR_TYPES else None


def _normalize_ref_for_type(mtype, raw_ref):
    ref = str(raw_ref or "").strip()
    if not ref:
        return ""
    if mtype in ("server", "desktop") and "|" in ref:
        return ref.split("|", 1)[0].strip()
    if mtype == "snmp" and "::" in ref:
        # Interface state often uses host::ifDescr; we keep host part as best effort.
        return ref.split("::", 1)[0].strip()
    return ref


def _collect_active_alert_candidates(customer_id):
    out = []

    # Active device status alerts.
    dq = DeviceStatusAlert.query.filter(
        DeviceStatusAlert.is_active.is_(True),
        DeviceStatusAlert.last_status == "DOWN",
    )
    if hasattr(DeviceStatusAlert, "customer_id"):
        dq = dq.filter(DeviceStatusAlert.customer_id == customer_id)
    for a in dq.all():
        mtype = _normalize_monitor_type(a.source)
        ref = _normalize_ref_for_type(mtype, a.device) if mtype else ""
        if not mtype or not ref:
            continue
        out.append(
            {
                "monitor_type": mtype,
                "monitor_ref": ref,
                "source_kind": "device_status",
                "source_id": a.id,
                "source": a.source,
                "state": a.last_status,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
        )

    # Active rule states.
    rq = (
        db.session.query(AlertRuleState, AlertRule)
        .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
        .filter(AlertRuleState.is_active.is_(True))
        .filter(AlertRule.customer_id == customer_id)
        .order_by(AlertRuleState.updated_at.desc())
    )
    for st, rule in rq.all():
        mtype = _normalize_monitor_type(rule.monitoring_type)
        if not mtype:
            continue

        ref = _normalize_ref_for_type(mtype, st.target_value)
        ext = st.extended_state or {}
        if not ref and isinstance(ext, dict):
            ref = (
                _normalize_ref_for_type(mtype, ext.get("instance"))
                or _normalize_ref_for_type(mtype, ext.get("host"))
                or _normalize_ref_for_type(mtype, ext.get("url"))
                or _normalize_ref_for_type(mtype, ext.get("device"))
            )
        if not ref:
            continue

        out.append(
            {
                "monitor_type": mtype,
                "monitor_ref": ref,
                "source_kind": "rule_state",
                "source_id": st.id,
                "rule_id": rule.id,
                "rule_name": rule.name,
                "state": "ACTIVE",
                "updated_at": st.updated_at.isoformat() if st.updated_at else None,
            }
        )

    # De-duplicate by monitor identity, keep newest updated_at.
    dedup = {}
    for c in out:
        key = f"{c['monitor_type']}::{c['monitor_ref']}"
        existing = dedup.get(key)
        if not existing:
            dedup[key] = c
            continue
        prev_ts = existing.get("updated_at") or ""
        curr_ts = c.get("updated_at") or ""
        if curr_ts > prev_ts:
            dedup[key] = c
    return list(dedup.values())


def _service_recommendations(services, monitor_type, monitor_ref):
    ref_l = (monitor_ref or "").lower()
    out = []
    for s in services:
        score = 0
        st = (s.service_type or "").lower()
        sn = (s.name or "").lower()
        if st and monitor_type in st:
            score += 4
        if monitor_type in sn:
            score += 3
        if sn and (sn in ref_l or ref_l in sn):
            score += 2
        if (s.criticality or "").lower() == "critical":
            score += 1
        out.append({"service_id": s.id, "service_name": s.name, "score": score})
    out.sort(key=lambda x: x["score"], reverse=True)
    return [x for x in out if x["score"] > 0][:3]


def _active_alert_keys(customer_id=None):
    key = f"itom:active_alert_keys:{customer_id}"

    def _build():
        keys = set()

        # Device up/down active alarms.
        dq = DeviceStatusAlert.query.filter(
            DeviceStatusAlert.is_active.is_(True),
            DeviceStatusAlert.last_status == "DOWN",
        )
        if customer_id is not None and hasattr(DeviceStatusAlert, "customer_id"):
            dq = dq.filter(DeviceStatusAlert.customer_id == customer_id)

        for source, device in dq.with_entities(DeviceStatusAlert.source, DeviceStatusAlert.device).all():
            dev_key = _normalize_key(device)
            if not dev_key:
                continue
            keys.add(dev_key)
            keys.add(f"{_normalize_key(source)}:{dev_key}")

        # Active rule states.
        q = (
            db.session.query(AlertRuleState, AlertRule.monitoring_type)
            .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
            .filter(AlertRuleState.is_active.is_(True))
        )
        if customer_id is not None:
            q = q.filter(AlertRule.customer_id == customer_id)
        else:
            q = _scope_query(q, AlertRule)

        for state, mtype in q.all():
            mt = _normalize_key(mtype)
            target = _normalize_key(state.target_value)
            if target:
                keys.add(target)
                keys.add(f"{mt}:{target}")

            ext = state.extended_state or {}
            if isinstance(ext, dict):
                for k in ("instance", "device", "host", "url", "server"):
                    val = _normalize_key(ext.get(k))
                    if not val:
                        continue
                    keys.add(val)
                    keys.add(f"{mt}:{val}")

        return keys

    return cached(key, 15, _build)


def _compute_service_health(services, bindings, deps, active_keys=None):
    customer_id = services[0].customer_id if services else None
    if active_keys is None:
        active_keys = _active_alert_keys(customer_id=customer_id)

    bindings_by_service = defaultdict(list)
    for b in bindings:
        bindings_by_service[b.service_id].append(b)

    deps_by_parent = defaultdict(list)
    reverse_deps = defaultdict(list)
    for d in deps:
        deps_by_parent[d.parent_service_id].append(d)
        reverse_deps[d.child_service_id].append(d.parent_service_id)

    status = {}
    reasons = defaultdict(list)

    # 1) Direct health from monitor bindings.
    for svc in services:
        down_hits = []
        for b in bindings_by_service.get(svc.id, []):
            if _binding_is_active(b, active_keys):
                down_hits.append(
                    {
                        "binding_id": b.id,
                        "monitor_type": b.monitor_type,
                        "monitor_ref": b.monitor_ref,
                        "display_name": b.display_name,
                        "reason": "active_alert",
                    }
                )

        if down_hits:
            status[svc.id] = "DOWN"
            reasons[svc.id].extend(down_hits)
        else:
            status[svc.id] = "UP"

    # 2) Dependency propagation.
    # Parent depends on child. If child is DOWN/IMPACTED -> parent IMPACTED (hard) or DEGRADED (soft).
    changed = True
    rounds = 0
    max_rounds = max(1, len(services) * 2)
    while changed and rounds < max_rounds:
        rounds += 1
        changed = False
        for svc in services:
            sid = svc.id
            if status.get(sid) == "DOWN":
                continue

            dep_edges = deps_by_parent.get(sid, [])
            hard_impact = False
            soft_impact = False
            impacted_by = []
            for edge in dep_edges:
                child_state = status.get(edge.child_service_id, "UP")
                if child_state in ("DOWN", "IMPACTED"):
                    impacted_by.append(
                        {
                            "dependency_id": edge.id,
                            "dependency_type": edge.dependency_type,
                            "child_service_id": edge.child_service_id,
                            "child_state": child_state,
                        }
                    )
                    if (edge.dependency_type or "hard").lower() == "hard":
                        hard_impact = True
                    else:
                        soft_impact = True

            new_state = status.get(sid, "UP")
            if hard_impact:
                new_state = "IMPACTED"
            elif soft_impact and new_state == "UP":
                new_state = "DEGRADED"

            if new_state != status.get(sid):
                status[sid] = new_state
                changed = True

            if impacted_by:
                reasons[sid].extend(impacted_by)

    # 3) Reverse dependency graph for "affected services"
    affected_by_service = defaultdict(set)
    for svc in services:
        sid = svc.id
        if status.get(sid) != "DOWN":
            continue
        # BFS over reverse dependencies.
        queue = [sid]
        seen = {sid}
        while queue:
            curr = queue.pop(0)
            for parent_id in reverse_deps.get(curr, []):
                if parent_id in seen:
                    continue
                seen.add(parent_id)
                queue.append(parent_id)
                affected_by_service[sid].add(parent_id)

    return status, reasons, affected_by_service


def _application_health_payload(app_obj):
    services = list(app_obj.services or [])
    service_ids = [s.id for s in services]
    deps = (
        ServiceDependency.query.filter(
            ServiceDependency.parent_service_id.in_(service_ids)
        ).all()
        if service_ids
        else []
    )
    bindings = [b for s in services for b in (s.bindings or [])]

    state_map, reasons_map, affected_by_service = _compute_service_health(
        services, bindings, deps
    )

    service_index = {s.id: s for s in services}
    out_services = []
    summary = {
        "total_services": len(services),
        "up": 0,
        "down": 0,
        "impacted": 0,
        "degraded": 0,
    }
    for svc in services:
        st = state_map.get(svc.id, "UP")
        if st == "DOWN":
            summary["down"] += 1
        elif st == "IMPACTED":
            summary["impacted"] += 1
        elif st == "DEGRADED":
            summary["degraded"] += 1
        else:
            summary["up"] += 1

        affected = []
        for parent_id in sorted(affected_by_service.get(svc.id, set())):
            pobj = service_index.get(parent_id)
            affected.append(
                {
                    "service_id": parent_id,
                    "service_name": pobj.name if pobj else str(parent_id),
                }
            )

        out_services.append(
            {
                "service": svc.to_dict(),
                "health": st,
                "reasons": reasons_map.get(svc.id, []),
                "affected_services": affected,
            }
        )

    app_health = "UP"
    if summary["down"] > 0:
        app_health = "DOWN"
    elif summary["impacted"] > 0:
        app_health = "IMPACTED"
    elif summary["degraded"] > 0:
        app_health = "DEGRADED"

    return {
        "application": app_obj.to_dict(),
        "application_health": app_health,
        "summary": summary,
        "services": out_services,
    }


@itom_bp.get("/api/itom/applications")
@security.login_required_api
def list_applications():
    q = _scope_query(BusinessApplication.query, BusinessApplication)
    items = q.order_by(BusinessApplication.created_at.desc()).all()
    return jsonify({"ok": True, "items": [x.to_dict() for x in items]})


@itom_bp.get("/itom/applications")
@itom_bp.get("/itom/dashboard")
@security.login_required_page
def applications_page():
    return render_template("itom_applications.html")


@itom_bp.get("/api/itom/customers")
@security.login_required_api
def itom_customers():
    allowed = _allowed_customer_id()
    if allowed is None:
        rows = Customer.query.order_by(Customer.name.asc()).all()
        return jsonify({"ok": True, "items": [x.to_dict() for x in rows]})

    row = Customer.query.get(allowed)
    items = [row.to_dict()] if row else []
    return jsonify({"ok": True, "items": items})


@itom_bp.get("/api/itom/monitor-types")
@security.login_required_api
def monitor_types():
    return jsonify({"ok": True, "items": list(MONITOR_TYPES)})


@itom_bp.get("/api/itom/monitor-options")
@security.login_required_api
def monitor_options():
    app_id = request.args.get("app_id", type=int)
    monitor_type = (request.args.get("monitor_type") or "").strip().lower()
    if not app_id:
        return jsonify({"ok": False, "error": "app_id is required"}), 400
    if monitor_type not in MONITOR_TYPES:
        return jsonify({"ok": False, "error": "unsupported monitor_type"}), 400

    app_obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    items = _monitor_options_for(app_obj.customer_id, monitor_type)
    return jsonify(
        {
            "ok": True,
            "app_id": app_obj.id,
            "customer_id": app_obj.customer_id,
            "monitor_type": monitor_type,
            "items": items,
        }
    )


@itom_bp.post("/api/itom/applications")
@security.login_required_api
def create_application():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400

    customer_id = _effective_customer_id(data.get("customer_id"))
    if not customer_id:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    obj = BusinessApplication(
        customer_id=customer_id,
        name=name,
        code=(data.get("code") or "").strip() or None,
        owner=(data.get("owner") or "").strip() or None,
        tier=(data.get("tier") or "").strip() or None,
        description=(data.get("description") or "").strip() or None,
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(obj)
    db.session.commit()
    return jsonify({"ok": True, "item": obj.to_dict()}), 201


@itom_bp.put("/api/itom/applications/<int:app_id>")
@security.login_required_api
def update_application(app_id):
    obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    if "name" in data:
        obj.name = (data.get("name") or "").strip() or obj.name
    if "code" in data:
        obj.code = (data.get("code") or "").strip() or None
    if "owner" in data:
        obj.owner = (data.get("owner") or "").strip() or None
    if "tier" in data:
        obj.tier = (data.get("tier") or "").strip() or None
    if "description" in data:
        obj.description = (data.get("description") or "").strip() or None
    if "is_active" in data:
        obj.is_active = bool(data.get("is_active"))

    if _is_admin() and "customer_id" in data and data.get("customer_id"):
        obj.customer_id = data.get("customer_id")

    db.session.commit()
    return jsonify({"ok": True, "item": obj.to_dict()})


@itom_bp.delete("/api/itom/applications/<int:app_id>")
@security.login_required_api
def delete_application(app_id):
    obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    db.session.delete(obj)
    db.session.commit()
    return jsonify({"ok": True})


@itom_bp.get("/api/itom/applications/<int:app_id>/topology")
@security.login_required_api
def application_topology(app_id):
    app_obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    services = (
        ApplicationService.query.filter_by(application_id=app_obj.id)
        .order_by(ApplicationService.name.asc())
        .all()
    )
    service_ids = [x.id for x in services]
    bindings = (
        ServiceBinding.query.filter(ServiceBinding.service_id.in_(service_ids)).all()
        if service_ids
        else []
    )
    deps = (
        ServiceDependency.query.filter(
            ServiceDependency.parent_service_id.in_(service_ids)
        ).all()
        if service_ids
        else []
    )
    return jsonify(
        {
            "ok": True,
            "application": app_obj.to_dict(),
            "services": [s.to_dict() for s in services],
            "bindings": [b.to_dict() for b in bindings],
            "dependencies": [d.to_dict() for d in deps],
        }
    )


@itom_bp.get("/api/itom/applications/<int:app_id>/dashboard")
@security.login_required_api
def application_dashboard(app_id):
    app_obj = (
        BusinessApplication.query.options(
            joinedload(BusinessApplication.services).joinedload(ApplicationService.bindings)
        )
        .filter(BusinessApplication.id == app_id)
        .first_or_404()
    )
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    services = list(app_obj.services or [])
    service_ids = [x.id for x in services]
    dependencies = (
        ServiceDependency.query.filter(
            ServiceDependency.parent_service_id.in_(service_ids)
        ).all()
        if service_ids
        else []
    )
    bindings = [b for s in services for b in (s.bindings or [])]
    health = _application_health_payload(app_obj)

    return jsonify(
        {
            "ok": True,
            "application": app_obj.to_dict(),
            "topology": {
                "services": [x.to_dict() for x in services],
                "bindings": [x.to_dict() for x in bindings],
                "dependencies": [x.to_dict() for x in dependencies],
            },
            "health": {
                "application_health": health["application_health"],
                "summary": health["summary"],
                "services": health["services"],
            },
        }
    )


@itom_bp.get("/api/itom/applications/<int:app_id>/layout")
@security.login_required_api
def application_layout_get(app_id):
    app_obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    row = ItomGraphLayout.query.filter_by(application_id=app_obj.id).first()
    return jsonify(
        {
            "ok": True,
            "application_id": app_obj.id,
            "layout": (row.layout_json if row else {}),
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            "updated_by_user_id": row.updated_by_user_id if row else None,
        }
    )


@itom_bp.post("/api/itom/applications/<int:app_id>/layout")
@security.login_required_api
def application_layout_save(app_id):
    app_obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    layout = data.get("layout")
    if not isinstance(layout, dict):
        return jsonify({"ok": False, "error": "layout must be an object"}), 400
    if len(layout) > 5000:
        return jsonify({"ok": False, "error": "layout too large"}), 400

    row = ItomGraphLayout.query.filter_by(application_id=app_obj.id).first()
    if not row:
        row = ItomGraphLayout(
            application_id=app_obj.id,
            customer_id=app_obj.customer_id,
            layout_json=layout,
            updated_by_user_id=(_current_user().id if _current_user() else None),
        )
        db.session.add(row)
    else:
        row.layout_json = layout
        row.customer_id = app_obj.customer_id
        row.updated_by_user_id = _current_user().id if _current_user() else None

    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@itom_bp.delete("/api/itom/applications/<int:app_id>/layout")
@security.login_required_api
def application_layout_delete(app_id):
    app_obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    row = ItomGraphLayout.query.filter_by(application_id=app_obj.id).first()
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify({"ok": True})


@itom_bp.get("/api/itom/applications/<int:app_id>/binding-quality")
@security.login_required_api
def application_binding_quality(app_id):
    app_obj = (
        BusinessApplication.query.options(
            joinedload(BusinessApplication.services).joinedload(ApplicationService.bindings)
        )
        .filter(BusinessApplication.id == app_id)
        .first_or_404()
    )
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    services = list(app_obj.services or [])
    all_bindings = [b for s in services for b in (s.bindings or [])]
    service_name_by_id = {s.id: s.name for s in services}

    stale = []
    valid = []
    dup_map = defaultdict(list)
    options_cache = {}

    for b in all_bindings:
        key = f"{_normalize_key(b.monitor_type)}::{_normalize_key(b.monitor_ref)}"
        dup_map[key].append(b.id)

        mtype = _normalize_key(b.monitor_type)
        if mtype not in MONITOR_TYPES:
            stale.append(
                {
                    "binding_id": b.id,
                    "service_id": b.service_id,
                    "service_name": service_name_by_id.get(b.service_id),
                    "monitor_type": b.monitor_type,
                    "monitor_ref": b.monitor_ref,
                    "reason": "unsupported_type",
                }
            )
            continue

        if mtype not in options_cache:
            options_cache[mtype] = {
                x["value"] for x in _monitor_options_for(app_obj.customer_id, mtype)
            }
        valid_refs = options_cache[mtype]
        if valid_refs and b.monitor_ref not in valid_refs:
            stale.append(
                {
                    "binding_id": b.id,
                    "service_id": b.service_id,
                    "service_name": service_name_by_id.get(b.service_id),
                    "monitor_type": b.monitor_type,
                    "monitor_ref": b.monitor_ref,
                    "reason": "monitor_not_found",
                }
            )
        else:
            valid.append(
                {
                    "binding_id": b.id,
                    "service_id": b.service_id,
                    "service_name": service_name_by_id.get(b.service_id),
                    "monitor_type": b.monitor_type,
                    "monitor_ref": b.monitor_ref,
                }
            )

    duplicates = [
        {"monitor_key": k, "binding_ids": ids}
        for k, ids in dup_map.items()
        if len(ids) > 1
    ]

    return jsonify(
        {
            "ok": True,
            "app_id": app_obj.id,
            "summary": {
                "total_bindings": len(all_bindings),
                "valid_bindings": len(valid),
                "stale_bindings": len(stale),
                "duplicate_keys": len(duplicates),
            },
            "stale_bindings": stale,
            "duplicates": duplicates,
        }
    )


@itom_bp.get("/api/itom/applications/<int:app_id>/binding-suggestions")
@security.login_required_api
def application_binding_suggestions(app_id):
    app_obj = (
        BusinessApplication.query.options(
            joinedload(BusinessApplication.services).joinedload(ApplicationService.bindings)
        )
        .filter(BusinessApplication.id == app_id)
        .first_or_404()
    )
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    services = list(app_obj.services or [])
    existing = {
        (str(b.monitor_type).strip().lower(), str(b.monitor_ref).strip())
        for s in services
        for b in (s.bindings or [])
    }
    options_cache = {}

    suggestions = []
    candidates = _collect_active_alert_candidates(app_obj.customer_id)
    for c in candidates:
        mtype = c["monitor_type"]
        ref = c["monitor_ref"]
        key = (mtype.lower(), ref)
        if key in existing:
            continue

        if mtype not in options_cache:
            options_cache[mtype] = {
                x["value"] for x in _monitor_options_for(app_obj.customer_id, mtype)
            }
        valid_refs = options_cache[mtype]
        if valid_refs and ref not in valid_refs:
            continue

        recos = _service_recommendations(services, mtype, ref)
        suggestions.append(
            {
                "monitor_type": mtype,
                "monitor_ref": ref,
                "display_name": f"{mtype}:{ref}",
                "source_kind": c.get("source_kind"),
                "source": c.get("source"),
                "rule_id": c.get("rule_id"),
                "rule_name": c.get("rule_name"),
                "updated_at": c.get("updated_at"),
                "recommended_services": recos,
                "confidence": "high" if recos else "medium",
            }
        )

    suggestions.sort(
        key=lambda x: (x["confidence"] == "high", x.get("updated_at") or ""),
        reverse=True,
    )

    return jsonify(
        {
            "ok": True,
            "app_id": app_obj.id,
            "count": len(suggestions),
            "items": suggestions[:100],
        }
    )


@itom_bp.post("/api/itom/applications/<int:app_id>/binding-suggestions/apply")
@security.login_required_api
def application_binding_suggestion_apply(app_id):
    app_obj = BusinessApplication.query.get_or_404(app_id)
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    service_id = data.get("service_id")
    monitor_type = (data.get("monitor_type") or "").strip().lower()
    monitor_ref = (data.get("monitor_ref") or "").strip()
    display_name = (data.get("display_name") or "").strip() or None

    if not service_id:
        return jsonify({"ok": False, "error": "service_id is required"}), 400
    if not monitor_type or not monitor_ref:
        return jsonify({"ok": False, "error": "monitor_type and monitor_ref are required"}), 400
    if monitor_type not in MONITOR_TYPES:
        return jsonify({"ok": False, "error": "unsupported monitor_type"}), 400

    svc = ApplicationService.query.get_or_404(service_id)
    if svc.application_id != app_obj.id:
        return jsonify({"ok": False, "error": "service does not belong to application"}), 400
    if not _service_belongs_to_scope(svc):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    valid_refs = {x["value"] for x in _monitor_options_for(app_obj.customer_id, monitor_type)}
    if valid_refs and monitor_ref not in valid_refs:
        return jsonify({"ok": False, "error": "invalid monitor_ref for monitor_type"}), 400

    existing = ServiceBinding.query.filter_by(
        service_id=svc.id,
        monitor_type=monitor_type,
        monitor_ref=monitor_ref,
    ).first()
    if existing:
        return jsonify({"ok": True, "existing": True, "item": existing.to_dict()})

    row = ServiceBinding(
        service_id=svc.id,
        customer_id=svc.customer_id,
        monitor_type=monitor_type,
        monitor_ref=monitor_ref,
        display_name=display_name,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "existing": False, "item": row.to_dict()}), 201


@itom_bp.post("/api/itom/services")
@security.login_required_api
def create_service():
    data = request.get_json(silent=True) or {}
    application_id = data.get("application_id")
    name = (data.get("name") or "").strip()
    if not application_id:
        return jsonify({"ok": False, "error": "application_id is required"}), 400
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400

    app_obj = BusinessApplication.query.get_or_404(application_id)
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    svc = ApplicationService(
        application_id=app_obj.id,
        customer_id=app_obj.customer_id,
        name=name,
        service_type=(data.get("service_type") or "").strip() or None,
        criticality=(data.get("criticality") or "high").strip().lower(),
        description=(data.get("description") or "").strip() or None,
        runbook_url=(data.get("runbook_url") or "").strip() or None,
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(svc)
    db.session.commit()
    return jsonify({"ok": True, "item": svc.to_dict()}), 201


@itom_bp.put("/api/itom/services/<int:service_id>")
@security.login_required_api
def update_service(service_id):
    svc = ApplicationService.query.get_or_404(service_id)
    if not _service_belongs_to_scope(svc):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    if "name" in data:
        svc.name = (data.get("name") or "").strip() or svc.name
    if "service_type" in data:
        svc.service_type = (data.get("service_type") or "").strip() or None
    if "criticality" in data:
        svc.criticality = (data.get("criticality") or "high").strip().lower()
    if "description" in data:
        svc.description = (data.get("description") or "").strip() or None
    if "runbook_url" in data:
        svc.runbook_url = (data.get("runbook_url") or "").strip() or None
    if "is_active" in data:
        svc.is_active = bool(data.get("is_active"))

    db.session.commit()
    return jsonify({"ok": True, "item": svc.to_dict()})


@itom_bp.delete("/api/itom/services/<int:service_id>")
@security.login_required_api
def delete_service(service_id):
    svc = ApplicationService.query.get_or_404(service_id)
    if not _service_belongs_to_scope(svc):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    db.session.delete(svc)
    db.session.commit()
    return jsonify({"ok": True})


@itom_bp.post("/api/itom/services/<int:service_id>/bindings")
@security.login_required_api
def create_binding(service_id):
    svc = ApplicationService.query.get_or_404(service_id)
    if not _service_belongs_to_scope(svc):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    monitor_type = (data.get("monitor_type") or "").strip().lower()
    monitor_ref = (data.get("monitor_ref") or "").strip()
    if not monitor_type:
        return jsonify({"ok": False, "error": "monitor_type is required"}), 400
    if not monitor_ref:
        return jsonify({"ok": False, "error": "monitor_ref is required"}), 400
    if monitor_type not in MONITOR_TYPES:
        return jsonify({"ok": False, "error": "unsupported monitor_type"}), 400

    valid_refs = {
        x["value"] for x in _monitor_options_for(svc.customer_id, monitor_type)
    }
    if valid_refs and monitor_ref not in valid_refs:
        return jsonify({"ok": False, "error": "invalid monitor_ref for type"}), 400

    row = ServiceBinding(
        service_id=svc.id,
        customer_id=svc.customer_id,
        monitor_type=monitor_type,
        monitor_ref=monitor_ref,
        display_name=(data.get("display_name") or "").strip() or None,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()}), 201


@itom_bp.delete("/api/itom/bindings/<int:binding_id>")
@security.login_required_api
def delete_binding(binding_id):
    row = ServiceBinding.query.get_or_404(binding_id)
    if not _service_belongs_to_scope(row):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@itom_bp.post("/api/itom/dependencies")
@security.login_required_api
def create_dependency():
    data = request.get_json(silent=True) or {}
    parent_service_id = data.get("parent_service_id")
    child_service_id = data.get("child_service_id")
    dep_type = (data.get("dependency_type") or "hard").strip().lower()
    if not parent_service_id or not child_service_id:
        return jsonify(
            {"ok": False, "error": "parent_service_id and child_service_id are required"}
        ), 400
    if parent_service_id == child_service_id:
        return jsonify({"ok": False, "error": "self dependency is not allowed"}), 400
    if dep_type not in ("hard", "soft"):
        return jsonify({"ok": False, "error": "dependency_type must be hard or soft"}), 400

    parent = ApplicationService.query.get_or_404(parent_service_id)
    child = ApplicationService.query.get_or_404(child_service_id)
    if not _service_belongs_to_scope(parent) or not _service_belongs_to_scope(child):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    if parent.customer_id != child.customer_id:
        return jsonify({"ok": False, "error": "cross-customer dependency not allowed"}), 400

    row = ServiceDependency(
        customer_id=parent.customer_id,
        parent_service_id=parent.id,
        child_service_id=child.id,
        dependency_type=dep_type,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()}), 201


@itom_bp.delete("/api/itom/dependencies/<int:dependency_id>")
@security.login_required_api
def delete_dependency(dependency_id):
    row = ServiceDependency.query.get_or_404(dependency_id)
    if not _service_belongs_to_scope(row):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@itom_bp.get("/api/itom/applications/<int:app_id>/health")
@security.login_required_api
def application_health(app_id):
    app_obj = (
        BusinessApplication.query.options(
            joinedload(BusinessApplication.services).joinedload(ApplicationService.bindings)
        )
        .filter(BusinessApplication.id == app_id)
        .first_or_404()
    )
    if not _service_belongs_to_scope(app_obj):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    payload = _application_health_payload(app_obj)

    return jsonify(
        {
            "ok": True,
            "application": payload["application"],
            "application_health": payload["application_health"],
            "summary": payload["summary"],
            "services": payload["services"],
        }
    )
