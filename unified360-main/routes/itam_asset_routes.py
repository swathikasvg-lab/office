import json
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from extensions import db
from models.itam import (
    ItamAsset,
    ItamComplianceFinding,
    ItamCompliancePolicy,
    ItamComplianceRun,
    ItamAssetLifecycle,
    ItamAssetItomBinding,
    ItamAssetRelation,
    ItamAssetSource,
    ItamAssetTag,
    ItamCloudIntegration,
    ItamDiscoveryRun,
)
from models.customer import Customer
from models.itom import ApplicationService, BusinessApplication, ServiceDependency
import security
from services.itam import (
    DEFAULT_SOURCES,
    SUPPORTED_SOURCES,
    get_or_create_policy,
    policy_code_from_name,
    run_discovery,
    run_compliance_evaluation,
    run_policy_now,
    update_policy,
    ensure_phase2_schema,
)
from services.itam.normalize import norm_lower, norm_str
from services.itam.risk import build_risk_report


itam_assets_bp = Blueprint("itam_assets", __name__)


def _current_user():
    return security.get_current_user()


def _allowed_customer_id():
    return security.get_allowed_customer_id(_current_user())


def _scope_query(query, model_cls):
    allowed = _allowed_customer_id()
    if allowed is None:
        return query
    return query.filter(getattr(model_cls, "customer_id") == allowed)


def _effective_customer_id(requested_customer_id):
    allowed = _allowed_customer_id()
    if allowed is None:
        return requested_customer_id
    return allowed


def _can_view():
    user = _current_user()
    if not user:
        return False
    return security.has_permission(user, "view_servers") or security.has_permission(
        user, "view_monitoring"
    )


def _can_manage():
    user = _current_user()
    if not user:
        return False
    return (
        security.has_permission(user, "edit_snmp")
        or security.has_permission(user, "manage_alerts")
        or security.has_permission(user, "manage_users")
    )


def _forbidden(permission_hint):
    return jsonify({"ok": False, "error": f"Forbidden: Missing permission '{permission_hint}'"}), 403


def _parse_tag_entries(tags):
    out = []
    seen = set()
    for raw in tags or []:
        key = "label"
        value = ""
        if isinstance(raw, str):
            value = norm_str(raw)
        elif isinstance(raw, dict):
            key = norm_lower(raw.get("key") or raw.get("name") or "label") or "label"
            value = norm_str(raw.get("value") or raw.get("tag"))
            if not value and key != "label":
                value = key
                key = "label"
        if not value:
            continue
        mark = (key, value)
        if mark in seen:
            continue
        seen.add(mark)
        out.append({"tag_key": key, "tag_value": value})
    return out


def _parse_date(value):
    s = norm_str(value)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _parse_datetime(value):
    s = norm_str(value)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _normalize_cloud_accounts(accounts, scoped_customer_id=None):
    out = []
    if not isinstance(accounts, list):
        return out
    for row in accounts:
        if not isinstance(row, dict):
            continue
        regions = row.get("regions")
        if isinstance(regions, str):
            regions = [x.strip() for x in regions.split(",") if x.strip()]
        if not isinstance(regions, list):
            regions = []

        provider = norm_lower(row.get("provider") or "aws")
        if provider not in {"aws", "azure", "gcp"}:
            provider = "aws"

        customer_id = scoped_customer_id
        if customer_id is None:
            try:
                customer_id = int(row.get("customer_id")) if row.get("customer_id") not in ("", None) else None
            except Exception:
                customer_id = None

        out.append(
            {
                "provider": provider,
                "customer_id": customer_id,
                "account_id": norm_str(row.get("account_id")),
                "role_arn": norm_str(row.get("role_arn")),
                "external_id": norm_str(row.get("external_id")),
                "profile": norm_str(row.get("profile")),
                "regions": [norm_str(x) for x in regions if norm_str(x)],
                "subscription_id": norm_str(row.get("subscription_id")),
                "tenant_id": norm_str(row.get("tenant_id")),
                "client_id": norm_str(row.get("client_id")),
                "client_secret": norm_str(row.get("client_secret")),
                "project_id": norm_str(row.get("project_id")),
                "credentials_json": row.get("credentials_json"),
                "credentials_file": norm_str(row.get("credentials_file")),
            }
        )
    return out


def _normalize_regions(value):
    if isinstance(value, str):
        values = [x.strip() for x in value.split(",") if x.strip()]
    elif isinstance(value, list):
        values = [norm_str(x) for x in value if norm_str(x)]
    else:
        values = []
    out = []
    seen = set()
    for item in values:
        v = norm_str(item)
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _normalize_credentials_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return s
        return s
    return ""


def _normalize_integration_config(provider, config):
    cfg = config if isinstance(config, dict) else {}
    p = norm_lower(provider)
    if p == "aws":
        return {
            "account_id": norm_str(cfg.get("account_id")),
            "role_arn": norm_str(cfg.get("role_arn")),
            "external_id": norm_str(cfg.get("external_id")),
            "profile": norm_str(cfg.get("profile")),
            "regions": _normalize_regions(cfg.get("regions")),
        }
    if p == "azure":
        return {
            "subscription_id": norm_str(cfg.get("subscription_id")),
            "tenant_id": norm_str(cfg.get("tenant_id")),
            "client_id": norm_str(cfg.get("client_id")),
            "client_secret": norm_str(cfg.get("client_secret")),
        }
    if p == "gcp":
        return {
            "project_id": norm_str(cfg.get("project_id")),
            "credentials_json": _normalize_credentials_json(cfg.get("credentials_json")),
            "credentials_file": norm_str(cfg.get("credentials_file")),
        }
    return {}


def _merge_masked_config(existing_cfg, incoming_cfg):
    old = existing_cfg if isinstance(existing_cfg, dict) else {}
    new = incoming_cfg if isinstance(incoming_cfg, dict) else {}
    merged = dict(new)
    for k, v in new.items():
        if isinstance(v, str) and "***" in v:
            merged[k] = old.get(k, "")
    return merged


def _validate_integration_config(provider, config):
    p = norm_lower(provider)
    if p == "aws":
        has_any = bool(
            norm_str(config.get("account_id"))
            or norm_str(config.get("role_arn"))
            or norm_str(config.get("profile"))
        )
        if not has_any:
            return "AWS config requires account_id or role_arn or profile"
        return ""
    if p == "azure":
        if not norm_str(config.get("subscription_id")):
            return "Azure config requires subscription_id"
        return ""
    if p == "gcp":
        if not norm_str(config.get("project_id")):
            return "GCP config requires project_id"
        return ""
    return "provider must be one of: aws, azure, gcp"


_MONITORING_SOURCE_HINTS = {"servers_cache", "desktop_cache", "snmp"}


def _normalized_sources_for_assets(asset_ids, customer_id=None):
    if not asset_ids:
        return {}

    query = _scope_query(ItamAssetSource.query, ItamAssetSource).filter(
        ItamAssetSource.asset_id.in_(asset_ids)
    )
    if customer_id is not None:
        query = query.filter(ItamAssetSource.customer_id == customer_id)

    rows = query.with_entities(ItamAssetSource.asset_id, ItamAssetSource.source_name).all()
    source_map = {}
    for aid, source_name in rows:
        source_map.setdefault(aid, set()).add((source_name or "").strip().lower())
    return source_map


def _bound_asset_ids(asset_ids, customer_id=None):
    if not asset_ids:
        return set()
    query = _scope_query(ItamAssetItomBinding.query, ItamAssetItomBinding).filter(
        ItamAssetItomBinding.asset_id.in_(asset_ids)
    )
    if customer_id is not None:
        query = query.filter(ItamAssetItomBinding.customer_id == customer_id)
    rows = query.with_entities(ItamAssetItomBinding.asset_id).distinct().all()
    return {x[0] for x in rows}


def _asset_relation_suggestions(customer_id=None, min_confidence=70):
    rel_query = _scope_query(ItamAssetRelation.query, ItamAssetRelation).filter(
        ItamAssetRelation.confidence >= int(min_confidence)
    )
    if customer_id is not None:
        rel_query = rel_query.filter(ItamAssetRelation.customer_id == customer_id)
    relations = rel_query.order_by(ItamAssetRelation.confidence.desc(), ItamAssetRelation.id.desc()).all()
    if not relations:
        return []

    asset_ids = set()
    for r in relations:
        asset_ids.add(r.from_asset_id)
        asset_ids.add(r.to_asset_id)

    binding_query = _scope_query(ItamAssetItomBinding.query, ItamAssetItomBinding).filter(
        ItamAssetItomBinding.asset_id.in_(list(asset_ids))
    )
    if customer_id is not None:
        binding_query = binding_query.filter(ItamAssetItomBinding.customer_id == customer_id)
    bindings = binding_query.all()

    by_asset = {}
    for row in bindings:
        by_asset.setdefault(row.asset_id, {"services": set(), "applications": set()})
        if row.service_id:
            by_asset[row.asset_id]["services"].add(row.service_id)
        if row.application_id:
            by_asset[row.asset_id]["applications"].add(row.application_id)

    suggestions = []
    seen = set()
    for rel in relations:
        left = by_asset.get(rel.from_asset_id) or {"services": set(), "applications": set()}
        right = by_asset.get(rel.to_asset_id) or {"services": set(), "applications": set()}
        for parent_service_id in left["services"]:
            for child_service_id in right["services"]:
                if parent_service_id == child_service_id:
                    continue
                key = (parent_service_id, child_service_id)
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append(
                    {
                        "relation_id": rel.id,
                        "from_asset_id": rel.from_asset_id,
                        "to_asset_id": rel.to_asset_id,
                        "parent_service_id": parent_service_id,
                        "child_service_id": child_service_id,
                        "confidence": int(rel.confidence or 0),
                        "relation_type": rel.relation_type or "depends_on",
                    }
                )
    return suggestions


def _scoped_policy_query(base_query, effective_customer_id=None):
    allowed = _allowed_customer_id()
    scoped_customer_id = allowed if allowed is not None else effective_customer_id
    if scoped_customer_id is None:
        return base_query
    return base_query.filter(
        db.or_(
            ItamCompliancePolicy.customer_id == scoped_customer_id,
            ItamCompliancePolicy.customer_id.is_(None),
        )
    )


@itam_assets_bp.get("/itam/assets")
@security.login_required_page
def itam_assets_page():
    if not _can_view():
        return "Forbidden", 403
    return render_template("itam_assets.html")


@itam_assets_bp.get("/api/itam/assets")
@security.login_required_api
def api_itam_assets_list():
    if not _can_view():
        return _forbidden("view_servers")

    q = (request.args.get("q") or "").strip()
    asset_type = (request.args.get("asset_type") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    source_name = (request.args.get("source_name") or "").strip().lower()
    customer_id = request.args.get("customer_id", type=int)
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(request.args.get("per_page", 50, type=int), 200))

    query = _scope_query(ItamAsset.query, ItamAsset)

    effective_customer_id = _effective_customer_id(customer_id)
    if effective_customer_id is not None:
        query = query.filter(ItamAsset.customer_id == effective_customer_id)

    if asset_type:
        query = query.filter(ItamAsset.asset_type == asset_type)
    if status:
        query = query.filter(ItamAsset.status == status)
    if source_name:
        query = (
            query.join(ItamAssetSource, ItamAssetSource.asset_id == ItamAsset.id)
            .filter(ItamAssetSource.source_name == source_name)
            .distinct()
        )
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                ItamAsset.asset_name.ilike(like),
                ItamAsset.hostname.ilike(like),
                ItamAsset.primary_ip.ilike(like),
                ItamAsset.primary_mac.ilike(like),
                ItamAsset.serial_number.ilike(like),
                ItamAsset.vendor.ilike(like),
                ItamAsset.model.ilike(like),
                ItamAsset.os_name.ilike(like),
                ItamAsset.location.ilike(like),
                ItamAsset.domain.ilike(like),
                ItamAsset.canonical_key.ilike(like),
            )
        )

    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    items = (
        query.order_by(ItamAsset.updated_at.desc(), ItamAsset.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return jsonify(
        {
            "ok": True,
            "items": [x.to_dict(include_details=False) for x in items],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
        }
    )


@itam_assets_bp.get("/api/itam/assets/<int:asset_id>")
@security.login_required_api
def api_itam_asset_detail(asset_id):
    if not _can_view():
        return _forbidden("view_servers")

    ensure_phase2_schema()

    row = _scope_query(ItamAsset.query, ItamAsset).filter(ItamAsset.id == asset_id).first()
    if not row:
        return jsonify({"ok": False, "error": "Asset not found"}), 404
    return jsonify({"ok": True, "item": row.to_dict(include_details=True)})


@itam_assets_bp.get("/api/itam/summary")
@security.login_required_api
def api_itam_summary():
    if not _can_view():
        return _forbidden("view_servers")

    customer_id = request.args.get("customer_id", type=int)
    effective_customer_id = _effective_customer_id(customer_id)

    query = _scope_query(ItamAsset.query, ItamAsset)
    if effective_customer_id is not None:
        query = query.filter(ItamAsset.customer_id == effective_customer_id)

    rows = query.all()
    by_type = {}
    by_status = {}
    for row in rows:
        by_type[row.asset_type] = by_type.get(row.asset_type, 0) + 1
        by_status[row.status] = by_status.get(row.status, 0) + 1

    source_query = _scope_query(ItamAssetSource.query, ItamAssetSource)
    if effective_customer_id is not None:
        source_query = source_query.filter(ItamAssetSource.customer_id == effective_customer_id)
    src_rows = (
        source_query.with_entities(
            ItamAssetSource.source_name, db.func.count(ItamAssetSource.id)
        )
        .group_by(ItamAssetSource.source_name)
        .all()
    )
    by_source = {name: int(count) for name, count in src_rows}

    return jsonify(
        {
            "ok": True,
            "total_assets": len(rows),
            "by_type": by_type,
            "by_status": by_status,
            "by_source_records": by_source,
        }
    )


@itam_assets_bp.get("/api/itam/coverage/summary")
@security.login_required_api
def api_itam_coverage_summary():
    if not _can_view():
        return _forbidden("view_servers")

    customer_id = request.args.get("customer_id", type=int)
    effective_customer_id = _effective_customer_id(customer_id)

    query = _scope_query(ItamAsset.query, ItamAsset)
    if effective_customer_id is not None:
        query = query.filter(ItamAsset.customer_id == effective_customer_id)
    assets = query.all()

    asset_ids = [x.id for x in assets]
    source_map = _normalized_sources_for_assets(asset_ids, customer_id=effective_customer_id)
    bound_ids = _bound_asset_ids(asset_ids, customer_id=effective_customer_id)

    total = len(assets)
    monitored = 0
    bound = 0
    by_type = {}

    for asset in assets:
        sources = source_map.get(asset.id, set())
        is_monitored = bool(sources & _MONITORING_SOURCE_HINTS)
        is_bound = asset.id in bound_ids

        if is_monitored:
            monitored += 1
        if is_bound:
            bound += 1

        key = asset.asset_type or "unknown"
        row = by_type.setdefault(
            key,
            {
                "total": 0,
                "monitored": 0,
                "unmonitored": 0,
                "bound": 0,
                "unbound": 0,
            },
        )
        row["total"] += 1
        row["monitored"] += 1 if is_monitored else 0
        row["unmonitored"] += 0 if is_monitored else 1
        row["bound"] += 1 if is_bound else 0
        row["unbound"] += 0 if is_bound else 1

    return jsonify(
        {
            "ok": True,
            "total_assets": total,
            "monitoring_covered_assets": monitored,
            "monitoring_coverage_pct": round((monitored * 100.0 / total), 2) if total else 0.0,
            "monitoring_gap_assets": max(0, total - monitored),
            "itom_bound_assets": bound,
            "itom_binding_pct": round((bound * 100.0 / total), 2) if total else 0.0,
            "itom_unbound_assets": max(0, total - bound),
            "by_type": by_type,
        }
    )


@itam_assets_bp.get("/api/itam/coverage/gaps")
@security.login_required_api
def api_itam_coverage_gaps():
    if not _can_view():
        return _forbidden("view_servers")

    customer_id = request.args.get("customer_id", type=int)
    effective_customer_id = _effective_customer_id(customer_id)
    limit = max(1, min(request.args.get("limit", 100, type=int), 500))
    active_only = str(request.args.get("active_only", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
    }

    query = _scope_query(ItamAsset.query, ItamAsset)
    if effective_customer_id is not None:
        query = query.filter(ItamAsset.customer_id == effective_customer_id)
    if active_only:
        query = query.filter(ItamAsset.status == "active")

    # Pull a larger window first, then keep only assets with actual gaps.
    assets = query.order_by(ItamAsset.last_seen.desc(), ItamAsset.id.desc()).limit(limit * 3).all()
    asset_ids = [x.id for x in assets]
    source_map = _normalized_sources_for_assets(asset_ids, customer_id=effective_customer_id)
    bound_ids = _bound_asset_ids(asset_ids, customer_id=effective_customer_id)

    items = []
    for asset in assets:
        sources = sorted(source_map.get(asset.id, set()))
        is_monitored = bool(set(sources) & _MONITORING_SOURCE_HINTS)
        is_bound = asset.id in bound_ids

        reasons = []
        suggestions = []
        if not is_monitored:
            reasons.append("no_monitoring_coverage")
            if (asset.asset_type or "").lower() == "network_device":
                suggestions.append("attach_snmp_monitoring")
            elif (asset.asset_type or "").lower() == "workstation":
                suggestions.append("attach_desktop_monitoring")
            else:
                suggestions.append("attach_server_or_agent_monitoring")
        if not is_bound:
            reasons.append("not_bound_to_itom")
            suggestions.append("bind_to_application_or_service")

        if not reasons:
            continue

        items.append(
            {
                "asset_id": asset.id,
                "customer_id": asset.customer_id,
                "asset_name": asset.asset_name or asset.hostname or asset.canonical_key,
                "asset_type": asset.asset_type or "unknown",
                "status": asset.status,
                "primary_ip": asset.primary_ip or "",
                "source_names": sources,
                "reasons": reasons,
                "suggestions": suggestions,
                "last_seen": asset.last_seen.isoformat() if asset.last_seen else None,
            }
        )
        if len(items) >= limit:
            break

    return jsonify({"ok": True, "items": items, "total_returned": len(items)})


@itam_assets_bp.get("/api/itam/risk/summary")
@security.login_required_api
def api_itam_risk_summary():
    if not _can_view():
        return _forbidden("view_servers")

    customer_id = _effective_customer_id(request.args.get("customer_id", type=int))
    limit_assets = max(1, min(request.args.get("limit_assets", 2000, type=int), 20000))
    stale_days = max(1, min(request.args.get("stale_days", 14, type=int), 365))

    report = build_risk_report(
        customer_id=customer_id,
        limit_assets=limit_assets,
        stale_days=stale_days,
    )
    top = report.get("top_risks") or []
    drifts = report.get("drift_alerts") or []
    return jsonify(
        {
            "ok": True,
            "summary": report.get("summary") or {},
            "top_risks": top[:20],
            "drift_alerts": drifts[:20],
        }
    )


@itam_assets_bp.get("/api/itam/risk/assets")
@security.login_required_api
def api_itam_risk_assets():
    if not _can_view():
        return _forbidden("view_servers")

    customer_id = _effective_customer_id(request.args.get("customer_id", type=int))
    limit_assets = max(1, min(request.args.get("limit_assets", 2000, type=int), 20000))
    stale_days = max(1, min(request.args.get("stale_days", 14, type=int), 365))
    limit = max(1, min(request.args.get("limit", 100, type=int), 1000))
    min_risk = max(0, min(request.args.get("min_risk", 1, type=int), 100))
    severity = norm_lower(request.args.get("severity"))

    report = build_risk_report(
        customer_id=customer_id,
        limit_assets=limit_assets,
        stale_days=stale_days,
    )
    rows = report.get("top_risks") or []
    if severity in {"high", "medium", "low"}:
        rows = [x for x in rows if norm_lower(x.get("risk_severity")) == severity]
    rows = [x for x in rows if int(x.get("risk_score") or 0) >= min_risk]
    rows = rows[:limit]
    return jsonify({"ok": True, "items": rows, "total_returned": len(rows)})


@itam_assets_bp.get("/api/itam/drift/alerts")
@security.login_required_api
def api_itam_drift_alerts():
    if not _can_view():
        return _forbidden("view_servers")

    customer_id = _effective_customer_id(request.args.get("customer_id", type=int))
    limit_assets = max(1, min(request.args.get("limit_assets", 2000, type=int), 20000))
    stale_days = max(1, min(request.args.get("stale_days", 14, type=int), 365))
    limit = max(1, min(request.args.get("limit", 200, type=int), 2000))
    severity = norm_lower(request.args.get("severity"))

    report = build_risk_report(
        customer_id=customer_id,
        limit_assets=limit_assets,
        stale_days=stale_days,
    )
    rows = report.get("drift_alerts") or []
    if severity in {"high", "medium", "low"}:
        filtered = []
        for item in rows:
            flags = item.get("drift_flags") if isinstance(item.get("drift_flags"), list) else []
            if any(norm_lower(x.get("severity")) == severity for x in flags if isinstance(x, dict)):
                filtered.append(item)
        rows = filtered
    rows = rows[:limit]
    return jsonify({"ok": True, "items": rows, "total_returned": len(rows)})


@itam_assets_bp.get("/api/itam/integrations")
@security.login_required_api
def api_itam_integrations_list():
    if not _can_view():
        return _forbidden("view_servers")

    ensure_phase2_schema()
    provider = norm_lower(request.args.get("provider"))
    if provider and provider not in {"aws", "azure", "gcp"}:
        return jsonify({"ok": False, "error": "provider must be aws, azure, or gcp"}), 400

    customer_id = request.args.get("customer_id", type=int)
    effective_customer_id = _effective_customer_id(customer_id)
    include_disabled = str(request.args.get("include_disabled", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
    }

    query = _scope_query(ItamCloudIntegration.query, ItamCloudIntegration)
    if effective_customer_id is not None:
        query = query.filter(ItamCloudIntegration.customer_id == effective_customer_id)
    if provider:
        query = query.filter(ItamCloudIntegration.provider == provider)
    if not include_disabled:
        query = query.filter(ItamCloudIntegration.enabled.is_(True))

    rows = query.order_by(
        ItamCloudIntegration.provider.asc(),
        ItamCloudIntegration.name.asc(),
        ItamCloudIntegration.id.asc(),
    ).all()
    return jsonify({"ok": True, "items": [x.to_dict(include_secrets=False) for x in rows]})


@itam_assets_bp.post("/api/itam/integrations")
@security.login_required_api
def api_itam_integrations_create():
    if not _can_manage():
        return _forbidden("manage_alerts")

    ensure_phase2_schema()
    data = request.get_json(silent=True) or {}
    provider = norm_lower(data.get("provider"))
    if provider not in {"aws", "azure", "gcp"}:
        return jsonify({"ok": False, "error": "provider must be aws, azure, or gcp"}), 400

    name = norm_str(data.get("name"))
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400

    allowed = _allowed_customer_id()
    customer_raw = data.get("customer_id")
    customer_id = _effective_customer_id(customer_raw)
    if allowed is None:
        if customer_raw in ("", None):
            customer_id = None
        else:
            try:
                customer_id = int(customer_raw)
            except Exception:
                return jsonify({"ok": False, "error": "customer_id must be integer or null"}), 400

    if customer_id is None:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    if customer_id is not None:
        customer = Customer.query.filter(Customer.cid == customer_id).first()
        if not customer:
            return jsonify({"ok": False, "error": "Invalid customer_id"}), 404

    config_raw = data.get("config")
    if not isinstance(config_raw, dict):
        return jsonify({"ok": False, "error": "config must be an object"}), 400
    config = _normalize_integration_config(provider, config_raw)
    config_error = _validate_integration_config(provider, config)
    if config_error:
        return jsonify({"ok": False, "error": config_error}), 400

    exists = ItamCloudIntegration.query.filter(
        ItamCloudIntegration.customer_id == customer_id,
        ItamCloudIntegration.provider == provider,
        ItamCloudIntegration.name == name,
    ).first()
    if exists:
        return jsonify({"ok": False, "error": "Integration name already exists for this scope"}), 409

    row = ItamCloudIntegration(
        customer_id=customer_id,
        provider=provider,
        name=name,
        enabled=bool(data.get("enabled", True)),
        config_json=config,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict(include_secrets=False)}), 201


@itam_assets_bp.put("/api/itam/integrations/<int:integration_id>")
@security.login_required_api
def api_itam_integrations_update(integration_id):
    if not _can_manage():
        return _forbidden("manage_alerts")

    ensure_phase2_schema()
    row = _scope_query(ItamCloudIntegration.query, ItamCloudIntegration).filter(
        ItamCloudIntegration.id == integration_id
    ).first()
    if not row:
        return jsonify({"ok": False, "error": "Integration not found"}), 404

    data = request.get_json(silent=True) or {}

    if "provider" in data:
        provider = norm_lower(data.get("provider"))
        if provider != row.provider:
            return jsonify({"ok": False, "error": "provider cannot be changed"}), 400

    if "name" in data:
        name = norm_str(data.get("name"))
        if not name:
            return jsonify({"ok": False, "error": "name cannot be empty"}), 400
        row.name = name

    if "enabled" in data:
        row.enabled = bool(data.get("enabled"))

    allowed = _allowed_customer_id()
    if "customer_id" in data:
        if allowed is not None:
            row.customer_id = allowed
        else:
            cid_raw = data.get("customer_id")
            if cid_raw in ("", None):
                return jsonify({"ok": False, "error": "customer_id cannot be null"}), 400
            else:
                try:
                    row.customer_id = int(cid_raw)
                except Exception:
                    return jsonify({"ok": False, "error": "customer_id must be integer or null"}), 400
                customer = Customer.query.filter(Customer.cid == row.customer_id).first()
                if not customer:
                    return jsonify({"ok": False, "error": "Invalid customer_id"}), 404

    if "config" in data:
        cfg_raw = data.get("config")
        if not isinstance(cfg_raw, dict):
            return jsonify({"ok": False, "error": "config must be an object"}), 400
        normalized = _normalize_integration_config(row.provider, cfg_raw)
        merged = _merge_masked_config(row.config_json or {}, normalized)
        cfg_error = _validate_integration_config(row.provider, merged)
        if cfg_error:
            return jsonify({"ok": False, "error": cfg_error}), 400
        row.config_json = merged

    exists = ItamCloudIntegration.query.filter(
        ItamCloudIntegration.id != row.id,
        ItamCloudIntegration.customer_id == row.customer_id,
        ItamCloudIntegration.provider == row.provider,
        ItamCloudIntegration.name == row.name,
    ).first()
    if exists:
        return jsonify({"ok": False, "error": "Integration name already exists for this scope"}), 409

    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict(include_secrets=False)})


@itam_assets_bp.delete("/api/itam/integrations/<int:integration_id>")
@security.login_required_api
def api_itam_integrations_delete(integration_id):
    if not _can_manage():
        return _forbidden("manage_alerts")

    ensure_phase2_schema()
    row = _scope_query(ItamCloudIntegration.query, ItamCloudIntegration).filter(
        ItamCloudIntegration.id == integration_id
    ).first()
    if not row:
        return jsonify({"ok": False, "error": "Integration not found"}), 404

    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@itam_assets_bp.post("/api/itam/discovery/run")
@security.login_required_api
def api_itam_discovery_run():
    if not _can_manage():
        return _forbidden("edit_snmp")

    data = request.get_json(silent=True) or {}
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        sources = list(DEFAULT_SOURCES)
    sources = [str(x).strip().lower() for x in sources if str(x).strip()]

    invalid = [x for x in sources if x not in SUPPORTED_SOURCES]
    if invalid:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Unsupported sources",
                    "supported": sorted(SUPPORTED_SOURCES),
                    "invalid": invalid,
                }
            ),
            400,
        )

    customer_id = _effective_customer_id(data.get("customer_id"))
    cloud_assets = data.get("cloud_assets") if isinstance(data.get("cloud_assets"), list) else None
    cloud_accounts = _normalize_cloud_accounts(data.get("cloud_accounts"), scoped_customer_id=customer_id)
    ot_assets = data.get("ot_assets") if isinstance(data.get("ot_assets"), list) else None
    if "cloud_manual" in sources and customer_id is None:
        return jsonify({"ok": False, "error": "customer_id is required for cloud_manual"}), 400
    if "ot_manual" in sources and customer_id is None:
        return jsonify({"ok": False, "error": "customer_id is required for ot_manual"}), 400

    run, summary = run_discovery(
        sources=sources,
        customer_id=customer_id,
        cloud_assets=cloud_assets,
        cloud_accounts=cloud_accounts,
        ot_assets=ot_assets,
    )
    return jsonify({"ok": True, "run": run.to_dict(), "summary": summary})


@itam_assets_bp.post("/api/itam/discovery/import-cloud")
@security.login_required_api
def api_itam_discovery_import_cloud():
    if not _can_manage():
        return _forbidden("edit_snmp")

    data = request.get_json(silent=True) or {}
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        return jsonify({"ok": False, "error": "assets must be a non-empty list"}), 400

    customer_id = _effective_customer_id(data.get("customer_id"))
    if customer_id is None:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    run, summary = run_discovery(
        sources=["cloud_manual"],
        customer_id=customer_id,
        cloud_assets=assets,
    )
    return jsonify({"ok": True, "run": run.to_dict(), "summary": summary})


@itam_assets_bp.post("/api/itam/discovery/import-ot")
@security.login_required_api
def api_itam_discovery_import_ot():
    if not _can_manage():
        return _forbidden("edit_snmp")

    data = request.get_json(silent=True) or {}
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        return jsonify({"ok": False, "error": "assets must be a non-empty list"}), 400

    customer_id = _effective_customer_id(data.get("customer_id"))
    if customer_id is None:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    run, summary = run_discovery(
        sources=["ot_manual"],
        customer_id=customer_id,
        ot_assets=assets,
    )
    return jsonify({"ok": True, "run": run.to_dict(), "summary": summary})


@itam_assets_bp.get("/api/itam/discovery/runs")
@security.login_required_api
def api_itam_discovery_runs():
    if not _can_view():
        return _forbidden("view_servers")

    limit = max(1, min(request.args.get("limit", 50, type=int), 300))
    customer_id = _effective_customer_id(request.args.get("customer_id", type=int))

    query = _scope_query(ItamDiscoveryRun.query, ItamDiscoveryRun)
    if customer_id is not None:
        query = query.filter(ItamDiscoveryRun.customer_id == customer_id)

    rows = query.order_by(ItamDiscoveryRun.started_at.desc()).limit(limit).all()
    return jsonify({"ok": True, "items": [x.to_dict() for x in rows]})


@itam_assets_bp.get("/api/itam/discovery/policy")
@security.login_required_api
def api_itam_discovery_policy_get():
    if not _can_view():
        return _forbidden("view_servers")

    row = get_or_create_policy()

    # Non-admin users are always scoped to their tenant for scheduler runs.
    allowed = _allowed_customer_id()
    if allowed is not None:
        if row.target_customer_id != allowed:
            row = update_policy(target_customer_id=allowed, set_target_customer=True)

    return jsonify({"ok": True, "item": row.to_dict(), "supported_sources": sorted(SUPPORTED_SOURCES)})


@itam_assets_bp.put("/api/itam/discovery/policy")
@security.login_required_api
def api_itam_discovery_policy_update():
    if not _can_manage():
        return _forbidden("edit_snmp")

    data = request.get_json(silent=True) or {}

    enabled = data.get("enabled")
    interval_minutes = data.get("interval_minutes")
    sources = data.get("sources")
    target_customer_id_raw = data.get("target_customer_id", "__unset__")
    target_customer_id = "__unset__"

    if enabled is not None:
        enabled = bool(enabled)

    if interval_minutes is not None:
        try:
            interval_minutes = int(interval_minutes)
        except Exception:
            return jsonify({"ok": False, "error": "interval_minutes must be an integer"}), 400
        if interval_minutes < 5 or interval_minutes > 1440:
            return jsonify({"ok": False, "error": "interval_minutes must be between 5 and 1440"}), 400

    if sources is not None:
        if not isinstance(sources, list):
            return jsonify({"ok": False, "error": "sources must be an array"}), 400
        normalized = []
        for x in sources:
            s = str(x or "").strip().lower()
            if s and s in SUPPORTED_SOURCES and s not in normalized:
                normalized.append(s)
        sources = normalized or list(DEFAULT_SOURCES)

    allowed = _allowed_customer_id()
    if target_customer_id_raw != "__unset__":
        if target_customer_id_raw in ("", None):
            target_customer_id = None
        else:
            try:
                target_customer_id = int(target_customer_id_raw)
            except Exception:
                return jsonify({"ok": False, "error": "target_customer_id must be an integer or null"}), 400

        if allowed is not None:
            target_customer_id = allowed
        elif target_customer_id is not None:
            customer = Customer.query.filter(Customer.cid == target_customer_id).first()
            if not customer:
                return jsonify({"ok": False, "error": "Invalid target_customer_id"}), 404

    row = update_policy(
        enabled=enabled,
        interval_minutes=interval_minutes,
        sources=sources,
        target_customer_id=(target_customer_id if target_customer_id != "__unset__" else None),
        set_target_customer=(target_customer_id != "__unset__"),
    )

    return jsonify({"ok": True, "item": row.to_dict()})


@itam_assets_bp.post("/api/itam/discovery/policy/run-now")
@security.login_required_api
def api_itam_discovery_policy_run_now():
    if not _can_manage():
        return _forbidden("edit_snmp")

    allowed = _allowed_customer_id()
    if allowed is not None:
        update_policy(target_customer_id=allowed, set_target_customer=True)

    result = run_policy_now()
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error") or "Run failed"}), 500

    return jsonify(
        {
            "ok": True,
            "policy": result["policy"].to_dict(),
            "run": result["run"].to_dict(),
            "summary": result["summary"],
        }
    )


@itam_assets_bp.get("/api/itam/assets/<int:asset_id>/tags")
@security.login_required_api
def api_itam_asset_tags_get(asset_id):
    if not _can_view():
        return _forbidden("view_servers")

    ensure_phase2_schema()
    asset = _scope_query(ItamAsset.query, ItamAsset).filter(ItamAsset.id == asset_id).first()
    if not asset:
        return jsonify({"ok": False, "error": "Asset not found"}), 404

    rows = (
        _scope_query(ItamAssetTag.query, ItamAssetTag)
        .filter(ItamAssetTag.asset_id == asset.id)
        .order_by(ItamAssetTag.tag_key.asc(), ItamAssetTag.tag_value.asc())
        .all()
    )
    return jsonify(
        {
            "ok": True,
            "asset_id": asset.id,
            "tags": asset.tags_json or [],
            "tag_records": [x.to_dict() for x in rows],
        }
    )


@itam_assets_bp.put("/api/itam/assets/<int:asset_id>/tags")
@security.login_required_api
def api_itam_asset_tags_put(asset_id):
    if not _can_manage():
        return _forbidden("edit_snmp")

    ensure_phase2_schema()
    asset = _scope_query(ItamAsset.query, ItamAsset).filter(ItamAsset.id == asset_id).first()
    if not asset:
        return jsonify({"ok": False, "error": "Asset not found"}), 404

    data = request.get_json(silent=True) or {}
    tags = data.get("tags")
    if not isinstance(tags, list):
        return jsonify({"ok": False, "error": "tags must be an array"}), 400

    mode = norm_lower(data.get("mode") or "merge")
    if mode not in ("merge", "replace"):
        return jsonify({"ok": False, "error": "mode must be merge or replace"}), 400

    entries = _parse_tag_entries(tags)
    now = datetime.utcnow()
    if mode == "replace":
        _scope_query(ItamAssetTag.query, ItamAssetTag).filter(ItamAssetTag.asset_id == asset.id).delete(
            synchronize_session=False
        )
        current_values = set()
    else:
        current_rows = (
            _scope_query(ItamAssetTag.query, ItamAssetTag)
            .filter(ItamAssetTag.asset_id == asset.id)
            .all()
        )
        current_values = {x.tag_value for x in current_rows if x.tag_value}

    for item in entries:
        key = item["tag_key"]
        value = item["tag_value"]
        current_values.add(value)
        row = _scope_query(ItamAssetTag.query, ItamAssetTag).filter(
            ItamAssetTag.asset_id == asset.id,
            ItamAssetTag.tag_key == key,
            ItamAssetTag.tag_value == value,
        ).first()
        if not row:
            row = ItamAssetTag(
                customer_id=asset.customer_id,
                asset_id=asset.id,
                tag_key=key,
                tag_value=value,
            )
            db.session.add(row)
        row.source_name = "manual_api"
        row.updated_at = now

    asset.tags_json = sorted(current_values)
    asset.updated_at = now
    db.session.commit()

    rows = (
        _scope_query(ItamAssetTag.query, ItamAssetTag)
        .filter(ItamAssetTag.asset_id == asset.id)
        .order_by(ItamAssetTag.tag_key.asc(), ItamAssetTag.tag_value.asc())
        .all()
    )
    return jsonify(
        {
            "ok": True,
            "asset_id": asset.id,
            "tags": asset.tags_json or [],
            "tag_records": [x.to_dict() for x in rows],
        }
    )


@itam_assets_bp.get("/api/itam/assets/<int:asset_id>/lifecycle")
@security.login_required_api
def api_itam_asset_lifecycle_get(asset_id):
    if not _can_view():
        return _forbidden("view_servers")

    ensure_phase2_schema()
    asset = _scope_query(ItamAsset.query, ItamAsset).filter(ItamAsset.id == asset_id).first()
    if not asset:
        return jsonify({"ok": False, "error": "Asset not found"}), 404

    limit = max(1, min(request.args.get("limit", 25, type=int), 200))
    rows = (
        _scope_query(ItamAssetLifecycle.query, ItamAssetLifecycle)
        .filter(ItamAssetLifecycle.asset_id == asset.id)
        .order_by(ItamAssetLifecycle.effective_at.desc(), ItamAssetLifecycle.id.desc())
        .limit(limit)
        .all()
    )
    current = next((x for x in rows if x.is_current), None)
    return jsonify(
        {
            "ok": True,
            "asset_id": asset.id,
            "current": current.to_dict() if current else None,
            "items": [x.to_dict() for x in rows],
        }
    )


@itam_assets_bp.put("/api/itam/assets/<int:asset_id>/lifecycle")
@security.login_required_api
def api_itam_asset_lifecycle_put(asset_id):
    if not _can_manage():
        return _forbidden("edit_snmp")

    ensure_phase2_schema()
    asset = _scope_query(ItamAsset.query, ItamAsset).filter(ItamAsset.id == asset_id).first()
    if not asset:
        return jsonify({"ok": False, "error": "Asset not found"}), 404

    data = request.get_json(silent=True) or {}
    current = (
        _scope_query(ItamAssetLifecycle.query, ItamAssetLifecycle)
        .filter(ItamAssetLifecycle.asset_id == asset.id, ItamAssetLifecycle.is_current.is_(True))
        .first()
    )

    stage = norm_lower(data.get("stage") or data.get("phase") or "") or (
        current.stage if current else "discovered"
    )
    status = norm_lower(data.get("status") or "") or (
        current.status if current else (asset.status or "active")
    )
    owner = norm_str(data.get("owner")) or (current.owner if current else "")
    cost_center = norm_str(data.get("cost_center")) or (current.cost_center if current else "")
    warranty_end = _parse_date(data.get("warranty_end")) or (current.warranty_end if current else None)
    eol_date = _parse_date(data.get("eol_date")) or (current.eol_date if current else None)
    decommission_date = _parse_date(data.get("decommission_date")) or (
        current.decommission_date if current else None
    )
    notes = norm_str(data.get("notes")) or (current.notes if current else "")
    tags = data.get("lifecycle_tags") if isinstance(data.get("lifecycle_tags"), list) else []
    if not tags and current:
        tags = current.lifecycle_tags_json or []
    lifecycle_tags = sorted({norm_str(x) for x in tags if norm_str(x)})
    source_name = norm_str(data.get("source_name") or "manual_api")
    effective_at = _parse_datetime(data.get("effective_at")) or datetime.utcnow()
    set_current = bool(data.get("set_current", True))

    same_as_current = bool(
        current
        and current.stage == stage
        and current.status == status
        and (current.owner or "") == owner
        and (current.cost_center or "") == cost_center
        and current.warranty_end == warranty_end
        and current.eol_date == eol_date
        and current.decommission_date == decommission_date
        and (current.notes or "") == notes
        and sorted(current.lifecycle_tags_json or []) == lifecycle_tags
    )

    if same_as_current:
        current.source_name = source_name
        current.effective_at = effective_at
        current.updated_at = datetime.utcnow()
        row = current
    else:
        if current and set_current:
            current.is_current = False
        row = ItamAssetLifecycle(
            customer_id=asset.customer_id,
            asset_id=asset.id,
            stage=stage,
            status=status,
            owner=owner,
            cost_center=cost_center,
            warranty_end=warranty_end,
            eol_date=eol_date,
            decommission_date=decommission_date,
            notes=notes,
            lifecycle_tags_json=lifecycle_tags,
            is_current=set_current,
            source_name=source_name,
            effective_at=effective_at,
        )
        db.session.add(row)

    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@itam_assets_bp.get("/api/itam/compliance/policies")
@security.login_required_api
def api_itam_compliance_policies():
    if not _can_view():
        return _forbidden("view_servers")

    ensure_phase2_schema()
    customer_id = _effective_customer_id(request.args.get("customer_id", type=int))
    include_disabled = str(request.args.get("include_disabled", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
    }

    query = _scoped_policy_query(ItamCompliancePolicy.query, customer_id)
    if not include_disabled:
        query = query.filter(ItamCompliancePolicy.enabled.is_(True))
    rows = query.order_by(ItamCompliancePolicy.severity.asc(), ItamCompliancePolicy.code.asc()).all()
    return jsonify({"ok": True, "items": [x.to_dict() for x in rows]})


@itam_assets_bp.post("/api/itam/compliance/policies")
@security.login_required_api
def api_itam_compliance_policy_create():
    if not _can_manage():
        return _forbidden("manage_alerts")

    ensure_phase2_schema()
    data = request.get_json(silent=True) or {}
    name = norm_str(data.get("name"))
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400

    allowed = _allowed_customer_id()
    requested_customer_id = data.get("customer_id")
    customer_id = _effective_customer_id(requested_customer_id)
    scope = norm_lower(data.get("scope"))
    if allowed is None and scope == "global":
        customer_id = None

    code = norm_lower(data.get("code")) or policy_code_from_name(name)
    if not code:
        code = policy_code_from_name(name)

    existing = ItamCompliancePolicy.query.filter(
        ItamCompliancePolicy.customer_id == customer_id,
        ItamCompliancePolicy.code == code,
    ).first()
    if existing:
        return jsonify({"ok": False, "error": "policy code already exists for this scope"}), 409

    severity = norm_lower(data.get("severity") or "medium")
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "medium"
    policy_type = norm_lower(data.get("policy_type") or "required_tag") or "required_tag"

    row = ItamCompliancePolicy(
        customer_id=customer_id,
        code=code,
        name=name,
        description=norm_str(data.get("description")) or None,
        severity=severity,
        enabled=bool(data.get("enabled", True)),
        policy_type=policy_type,
        criteria_json=data.get("criteria") if isinstance(data.get("criteria"), dict) else {},
        target_filters_json=(
            data.get("target_filters") if isinstance(data.get("target_filters"), dict) else {}
        ),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()}), 201


@itam_assets_bp.put("/api/itam/compliance/policies/<int:policy_id>")
@security.login_required_api
def api_itam_compliance_policy_update(policy_id):
    if not _can_manage():
        return _forbidden("manage_alerts")

    ensure_phase2_schema()
    row = ItamCompliancePolicy.query.filter(ItamCompliancePolicy.id == policy_id).first()
    if not row:
        return jsonify({"ok": False, "error": "Policy not found"}), 404

    allowed = _allowed_customer_id()
    if allowed is not None and row.customer_id != allowed:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = norm_str(data.get("name"))
        if not name:
            return jsonify({"ok": False, "error": "name cannot be empty"}), 400
        row.name = name
    if "description" in data:
        row.description = norm_str(data.get("description")) or None
    if "severity" in data:
        sev = norm_lower(data.get("severity"))
        if sev not in {"low", "medium", "high", "critical"}:
            return jsonify({"ok": False, "error": "invalid severity"}), 400
        row.severity = sev
    if "enabled" in data:
        row.enabled = bool(data.get("enabled"))
    if "policy_type" in data:
        ptype = norm_lower(data.get("policy_type"))
        if not ptype:
            return jsonify({"ok": False, "error": "policy_type cannot be empty"}), 400
        row.policy_type = ptype
    if "criteria" in data:
        if not isinstance(data.get("criteria"), dict):
            return jsonify({"ok": False, "error": "criteria must be an object"}), 400
        row.criteria_json = data.get("criteria")
    if "target_filters" in data:
        if not isinstance(data.get("target_filters"), dict):
            return jsonify({"ok": False, "error": "target_filters must be an object"}), 400
        row.target_filters_json = data.get("target_filters")
    if "code" in data:
        code = norm_lower(data.get("code"))
        if not code:
            return jsonify({"ok": False, "error": "code cannot be empty"}), 400
        exists = ItamCompliancePolicy.query.filter(
            ItamCompliancePolicy.id != row.id,
            ItamCompliancePolicy.customer_id == row.customer_id,
            ItamCompliancePolicy.code == code,
        ).first()
        if exists:
            return jsonify({"ok": False, "error": "policy code already exists for this scope"}), 409
        row.code = code

    if allowed is None and "customer_id" in data:
        cid = data.get("customer_id")
        if cid in ("", None):
            row.customer_id = None
        else:
            try:
                row.customer_id = int(cid)
            except Exception:
                return jsonify({"ok": False, "error": "customer_id must be integer or null"}), 400

    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@itam_assets_bp.get("/api/itam/compliance/runs")
@security.login_required_api
def api_itam_compliance_runs():
    if not _can_view():
        return _forbidden("view_servers")

    ensure_phase2_schema()
    limit = max(1, min(request.args.get("limit", 50, type=int), 300))
    customer_id = _effective_customer_id(request.args.get("customer_id", type=int))

    query = _scope_query(ItamComplianceRun.query, ItamComplianceRun)
    if customer_id is not None:
        query = query.filter(ItamComplianceRun.customer_id == customer_id)

    rows = query.order_by(ItamComplianceRun.started_at.desc()).limit(limit).all()
    return jsonify({"ok": True, "items": [x.to_dict() for x in rows]})


@itam_assets_bp.get("/api/itam/compliance/findings")
@security.login_required_api
def api_itam_compliance_findings():
    if not _can_view():
        return _forbidden("view_servers")

    ensure_phase2_schema()
    limit = max(1, min(request.args.get("limit", 200, type=int), 2000))
    customer_id = _effective_customer_id(request.args.get("customer_id", type=int))
    status = norm_lower(request.args.get("status"))
    policy_id = request.args.get("policy_id", type=int)
    asset_id = request.args.get("asset_id", type=int)
    failed_only = str(request.args.get("failed_only", "false")).strip().lower() in {"1", "true", "yes"}

    query = _scope_query(ItamComplianceFinding.query, ItamComplianceFinding)
    if customer_id is not None:
        query = query.filter(ItamComplianceFinding.customer_id == customer_id)
    if status:
        query = query.filter(ItamComplianceFinding.status == status)
    if policy_id:
        query = query.filter(ItamComplianceFinding.policy_id == policy_id)
    if asset_id:
        query = query.filter(ItamComplianceFinding.asset_id == asset_id)
    if failed_only:
        query = query.filter(ItamComplianceFinding.status.in_(["fail", "error"]))

    rows = query.order_by(ItamComplianceFinding.updated_at.desc()).limit(limit).all()
    return jsonify({"ok": True, "items": [x.to_dict() for x in rows]})


@itam_assets_bp.post("/api/itam/compliance/evaluate")
@security.login_required_api
def api_itam_compliance_evaluate():
    if not _can_manage():
        return _forbidden("manage_alerts")

    ensure_phase2_schema()
    data = request.get_json(silent=True) or {}
    customer_id = _effective_customer_id(data.get("customer_id"))

    policy_ids_raw = data.get("policy_ids")
    asset_ids_raw = data.get("asset_ids")
    policy_ids = [int(x) for x in policy_ids_raw if str(x).strip().isdigit()] if isinstance(policy_ids_raw, list) else None
    asset_ids = [int(x) for x in asset_ids_raw if str(x).strip().isdigit()] if isinstance(asset_ids_raw, list) else None

    try:
        limit_assets = int(data.get("limit_assets", 2000))
    except Exception:
        return jsonify({"ok": False, "error": "limit_assets must be integer"}), 400
    limit_assets = max(1, min(limit_assets, 20000))

    user = _current_user()
    triggered_by = user.username if user else "system"

    run, summary = run_compliance_evaluation(
        customer_id=customer_id,
        policy_ids=policy_ids,
        asset_ids=asset_ids,
        triggered_by=triggered_by,
        limit_assets=limit_assets,
    )
    return jsonify({"ok": True, "run": run.to_dict(), "summary": summary})


@itam_assets_bp.get("/api/itam/itom/dependency-suggestions")
@security.login_required_api
def api_itam_itom_dependency_suggestions():
    if not _can_view():
        return _forbidden("view_servers")

    customer_id = request.args.get("customer_id", type=int)
    effective_customer_id = _effective_customer_id(customer_id)
    min_confidence = max(1, min(request.args.get("min_confidence", 70, type=int), 100))
    limit = max(1, min(request.args.get("limit", 300, type=int), 1000))

    suggestions = _asset_relation_suggestions(
        customer_id=effective_customer_id,
        min_confidence=min_confidence,
    )
    if len(suggestions) > limit:
        suggestions = suggestions[:limit]

    service_ids = set()
    for row in suggestions:
        service_ids.add(row["parent_service_id"])
        service_ids.add(row["child_service_id"])

    service_name_map = {}
    if service_ids:
        svc_rows = (
            _scope_query(ApplicationService.query, ApplicationService)
            .filter(ApplicationService.id.in_(list(service_ids)))
            .all()
        )
        service_name_map = {x.id: x.name for x in svc_rows}

    existing_pairs = set()
    if service_ids:
        dep_query = _scope_query(ServiceDependency.query, ServiceDependency).filter(
            ServiceDependency.parent_service_id.in_(list(service_ids)),
            ServiceDependency.child_service_id.in_(list(service_ids)),
        )
        dep_rows = dep_query.with_entities(
            ServiceDependency.parent_service_id,
            ServiceDependency.child_service_id,
        ).all()
        existing_pairs = {(a, b) for a, b in dep_rows}

    items = []
    new_count = 0
    for row in suggestions:
        key = (row["parent_service_id"], row["child_service_id"])
        exists = key in existing_pairs
        if not exists:
            new_count += 1
        items.append(
            {
                **row,
                "parent_service_name": service_name_map.get(row["parent_service_id"], ""),
                "child_service_name": service_name_map.get(row["child_service_id"], ""),
                "already_exists": exists,
            }
        )

    return jsonify(
        {
            "ok": True,
            "min_confidence": min_confidence,
            "total_suggestions": len(items),
            "new_dependency_suggestions": new_count,
            "items": items,
        }
    )


@itam_assets_bp.post("/api/itam/itom/dependency-suggestions/apply")
@security.login_required_api
def api_itam_itom_dependency_suggestions_apply():
    if not _can_manage():
        return _forbidden("manage_alerts")

    data = request.get_json(silent=True) or {}
    customer_id = _effective_customer_id(data.get("customer_id"))
    min_confidence = data.get("min_confidence", 70)
    limit = data.get("limit", 500)
    try:
        min_confidence = max(1, min(int(min_confidence), 100))
        limit = max(1, min(int(limit), 2000))
    except Exception:
        return jsonify({"ok": False, "error": "min_confidence and limit must be integers"}), 400

    suggestions = _asset_relation_suggestions(
        customer_id=customer_id,
        min_confidence=min_confidence,
    )
    if len(suggestions) > limit:
        suggestions = suggestions[:limit]

    if not suggestions:
        return jsonify(
            {
                "ok": True,
                "created": 0,
                "skipped_existing": 0,
                "skipped_invalid": 0,
                "items": [],
            }
        )

    service_ids = set()
    for row in suggestions:
        service_ids.add(row["parent_service_id"])
        service_ids.add(row["child_service_id"])

    services = (
        _scope_query(ApplicationService.query, ApplicationService)
        .filter(ApplicationService.id.in_(list(service_ids)))
        .all()
    )
    service_map = {x.id: x for x in services}

    existing_query = _scope_query(ServiceDependency.query, ServiceDependency).filter(
        ServiceDependency.parent_service_id.in_(list(service_ids)),
        ServiceDependency.child_service_id.in_(list(service_ids)),
    )
    existing_pairs = {
        (a, b)
        for a, b in existing_query.with_entities(
            ServiceDependency.parent_service_id,
            ServiceDependency.child_service_id,
        ).all()
    }

    created = []
    skipped_existing = 0
    skipped_invalid = 0
    for row in suggestions:
        parent_service_id = row["parent_service_id"]
        child_service_id = row["child_service_id"]
        if parent_service_id == child_service_id:
            skipped_invalid += 1
            continue

        parent_service = service_map.get(parent_service_id)
        child_service = service_map.get(child_service_id)
        if not parent_service or not child_service:
            skipped_invalid += 1
            continue
        if parent_service.customer_id != child_service.customer_id:
            skipped_invalid += 1
            continue

        key = (parent_service_id, child_service_id)
        if key in existing_pairs:
            skipped_existing += 1
            continue

        dep = ServiceDependency(
            customer_id=parent_service.customer_id,
            parent_service_id=parent_service_id,
            child_service_id=child_service_id,
            dependency_type="soft",
        )
        db.session.add(dep)
        existing_pairs.add(key)
        created.append(
            {
                "parent_service_id": parent_service_id,
                "parent_service_name": parent_service.name,
                "child_service_id": child_service_id,
                "child_service_name": child_service.name,
                "inferred_confidence": row["confidence"],
                "inferred_relation_type": row["relation_type"],
            }
        )

    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "created": len(created),
            "skipped_existing": skipped_existing,
            "skipped_invalid": skipped_invalid,
            "items": created,
        }
    )


@itam_assets_bp.get("/api/itam/itom/options")
@security.login_required_api
def api_itam_itom_options():
    if not _can_view():
        return _forbidden("view_servers")

    apps = _scope_query(BusinessApplication.query, BusinessApplication).all()
    services = _scope_query(ApplicationService.query, ApplicationService).all()

    return jsonify(
        {
            "ok": True,
            "applications": [
                {"id": x.id, "name": x.name, "customer_id": x.customer_id}
                for x in apps
            ],
            "services": [
                {
                    "id": x.id,
                    "name": x.name,
                    "application_id": x.application_id,
                    "customer_id": x.customer_id,
                }
                for x in services
            ],
        }
    )


@itam_assets_bp.post("/api/itam/assets/<int:asset_id>/bind-itom")
@security.login_required_api
def api_itam_asset_bind_itom(asset_id):
    if not _can_manage():
        return _forbidden("manage_alerts")

    asset = _scope_query(ItamAsset.query, ItamAsset).filter(ItamAsset.id == asset_id).first()
    if not asset:
        return jsonify({"ok": False, "error": "Asset not found"}), 404

    data = request.get_json(silent=True) or {}
    service_id = data.get("service_id")
    application_id = data.get("application_id")
    relation_type = (data.get("relation_type") or "supports").strip().lower()
    confidence = int(data.get("confidence") or 70)

    app_obj = None
    svc_obj = None

    if service_id:
        svc_obj = (
            _scope_query(ApplicationService.query, ApplicationService)
            .filter(ApplicationService.id == int(service_id))
            .first()
        )
        if not svc_obj:
            return jsonify({"ok": False, "error": "Invalid service_id"}), 404
        app_obj = (
            _scope_query(BusinessApplication.query, BusinessApplication)
            .filter(BusinessApplication.id == svc_obj.application_id)
            .first()
        )
        application_id = app_obj.id if app_obj else None
    elif application_id:
        app_obj = (
            _scope_query(BusinessApplication.query, BusinessApplication)
            .filter(BusinessApplication.id == int(application_id))
            .first()
        )
        if not app_obj:
            return jsonify({"ok": False, "error": "Invalid application_id"}), 404
    else:
        return jsonify({"ok": False, "error": "service_id or application_id is required"}), 400

    row = ItamAssetItomBinding.query.filter(
        ItamAssetItomBinding.asset_id == asset.id,
        ItamAssetItomBinding.service_id == (svc_obj.id if svc_obj else None),
        ItamAssetItomBinding.application_id == (app_obj.id if app_obj else None),
    ).first()

    if not row:
        row = ItamAssetItomBinding(
            customer_id=asset.customer_id,
            asset_id=asset.id,
            application_id=app_obj.id if app_obj else None,
            service_id=svc_obj.id if svc_obj else None,
        )
        db.session.add(row)

    row.relation_type = relation_type
    row.confidence = max(1, min(confidence, 100))
    db.session.commit()

    return jsonify({"ok": True, "item": row.to_dict()})
