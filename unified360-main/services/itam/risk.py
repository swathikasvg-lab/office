from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import selectinload

from models.itam import ItamAsset
from services.itam.normalize import norm_lower, norm_str


def _days_since(value, now):
    if not value:
        return None
    try:
        return max(0, int((now - value).total_seconds() // 86400))
    except Exception:
        return None


def _current_lifecycle(asset):
    for row in asset.lifecycle_rows or []:
        if row.is_current:
            return row
    return None


def _value_set_from_sources(asset, key):
    values = set()
    for src in asset.sources or []:
        raw = src.raw_json if isinstance(src.raw_json, dict) else {}
        v = norm_lower(raw.get(key))
        if v:
            values.add(v)
    return values


def _source_conflicts(asset):
    fields = ("hostname", "primary_ip", "os_name", "status", "location")
    out = []
    for key in fields:
        values = _value_set_from_sources(asset, key)
        if len(values) > 1:
            out.append(key)
    return out


def _compliance_counts(asset):
    fail = 0
    error = 0
    for row in asset.compliance_findings or []:
        status = norm_lower(row.status)
        if status == "fail":
            fail += 1
        elif status == "error":
            error += 1
    return fail, error


def _quality_score(asset, now):
    identities = len(asset.identities or [])
    source_count = len(asset.sources or [])
    avg_conf = 0
    if source_count > 0:
        avg_conf = int(sum(int(s.confidence or 0) for s in (asset.sources or [])) / source_count)

    completeness_fields = (
        asset.asset_name or asset.hostname,
        asset.asset_type,
        asset.status,
        asset.primary_ip or asset.primary_mac or asset.serial_number,
        asset.os_name,
        asset.location or asset.environment,
    )
    filled = sum(1 for x in completeness_fields if norm_str(x))
    completeness = int((filled * 100) / len(completeness_fields))

    age_days = _days_since(asset.last_seen, now)
    if age_days is None:
        freshness = 0
    elif age_days <= 1:
        freshness = 100
    elif age_days <= 7:
        freshness = 85
    elif age_days <= 30:
        freshness = 65
    else:
        freshness = 40

    identity_strength = min(100, identities * 24)
    source_strength = min(100, source_count * 28)
    quality = int(
        (identity_strength * 0.22)
        + (source_strength * 0.18)
        + (avg_conf * 0.20)
        + (completeness * 0.20)
        + (freshness * 0.20)
    )
    return max(0, min(100, quality))


def _risk_for_asset(asset, now, stale_days=14):
    quality = _quality_score(asset, now)
    risk = 0
    reasons = []
    drift = []

    age_days = _days_since(asset.last_seen, now)
    lifecycle = _current_lifecycle(asset)
    conflicts = _source_conflicts(asset)
    fail_count, error_count = _compliance_counts(asset)

    if quality < 45:
        risk += 30
        reasons.append("low_data_quality")
    elif quality < 60:
        risk += 20
        reasons.append("medium_data_quality")

    if age_days is None:
        risk += 15
        reasons.append("never_seen")
    elif age_days > stale_days:
        risk += 12 if age_days <= 30 else 20
        reasons.append("stale_asset")
        drift.append(
            {
                "type": "stale",
                "severity": "medium" if age_days <= 30 else "high",
                "detail": f"last_seen_{age_days}_days_ago",
            }
        )

    if conflicts:
        penalty = min(20, len(conflicts) * 6)
        risk += penalty
        reasons.append("source_conflict")
        drift.append(
            {
                "type": "source_conflict",
                "severity": "medium" if len(conflicts) == 1 else "high",
                "detail": ",".join(conflicts),
            }
        )

    if fail_count or error_count:
        risk += min(35, (fail_count * 7) + (error_count * 10))
        reasons.append("compliance_failures")
        drift.append(
            {
                "type": "compliance_drift",
                "severity": "high" if fail_count + error_count >= 3 else "medium",
                "detail": f"fail={fail_count},error={error_count}",
            }
        )

    if lifecycle and lifecycle.decommission_date and norm_lower(asset.status) == "active":
        try:
            overdue = lifecycle.decommission_date <= now.date()
        except Exception:
            overdue = False
        if overdue:
            risk += 20
            reasons.append("lifecycle_overdue")
            drift.append(
                {
                    "type": "lifecycle_drift",
                    "severity": "high",
                    "detail": f"decommission_date={lifecycle.decommission_date.isoformat()}",
                }
            )

    risk = max(0, min(100, risk))
    if risk >= 70:
        severity = "high"
    elif risk >= 40:
        severity = "medium"
    elif risk > 0:
        severity = "low"
    else:
        severity = "none"

    return {
        "asset_id": asset.id,
        "customer_id": asset.customer_id,
        "asset_name": asset.asset_name or asset.hostname or asset.canonical_key,
        "asset_type": asset.asset_type or "unknown",
        "status": asset.status,
        "primary_ip": asset.primary_ip or "",
        "source_count": int(asset.source_count or 0),
        "quality_score": quality,
        "risk_score": risk,
        "risk_severity": severity,
        "risk_reasons": reasons,
        "drift_flags": drift,
        "compliance_fail_count": int(fail_count),
        "compliance_error_count": int(error_count),
        "last_seen": asset.last_seen.isoformat() if asset.last_seen else None,
        "lifecycle_stage": lifecycle.stage if lifecycle else "",
        "lifecycle_status": lifecycle.status if lifecycle else "",
    }


def build_risk_report(customer_id=None, limit_assets=2000, stale_days=14):
    now = datetime.now(timezone.utc)
    limit_assets = max(1, min(int(limit_assets or 2000), 20000))
    stale_days = max(1, min(int(stale_days or 14), 365))

    query = (
        ItamAsset.query.options(
            selectinload(ItamAsset.sources),
            selectinload(ItamAsset.identities),
            selectinload(ItamAsset.lifecycle_rows),
            selectinload(ItamAsset.compliance_findings),
        )
        .order_by(ItamAsset.updated_at.desc(), ItamAsset.id.desc())
    )
    if customer_id is not None:
        query = query.filter(ItamAsset.customer_id == customer_id)
    query = query.limit(limit_assets)

    assets = query.all()
    rows = [_risk_for_asset(x, now=now, stale_days=stale_days) for x in assets]

    high = sum(1 for x in rows if x["risk_severity"] == "high")
    medium = sum(1 for x in rows if x["risk_severity"] == "medium")
    low = sum(1 for x in rows if x["risk_severity"] == "low")
    drift_assets = sum(1 for x in rows if x["drift_flags"])
    compliance_assets = sum(1 for x in rows if (x["compliance_fail_count"] + x["compliance_error_count"]) > 0)
    avg_quality = round(sum(x["quality_score"] for x in rows) / len(rows), 2) if rows else 0.0

    stale_assets = 0
    stale_cutoff = now - timedelta(days=stale_days)
    for asset in assets:
        if asset.last_seen and asset.last_seen < stale_cutoff:
            stale_assets += 1

    top_risks = sorted(rows, key=lambda x: (x["risk_score"], x["quality_score"] * -1), reverse=True)
    drift_alerts = [x for x in top_risks if x["drift_flags"]]

    return {
        "summary": {
            "total_assets": len(rows),
            "avg_quality_score": avg_quality,
            "high_risk_assets": high,
            "medium_risk_assets": medium,
            "low_risk_assets": low,
            "drift_alert_assets": drift_assets,
            "compliance_risk_assets": compliance_assets,
            "stale_assets": stale_assets,
            "stale_days_threshold": stale_days,
        },
        "top_risks": top_risks,
        "drift_alerts": drift_alerts,
    }
