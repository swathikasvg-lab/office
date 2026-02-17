import re
from datetime import datetime, timezone

from extensions import db
from models.itam import (
    ItamAsset,
    ItamComplianceFinding,
    ItamCompliancePolicy,
    ItamComplianceRun,
)
from services.itam.normalize import norm_lower, norm_str
from services.itam.schema import ensure_phase2_schema


def policy_code_from_name(name):
    base = re.sub(r"[^a-z0-9]+", "_", norm_lower(name)).strip("_")
    return base or "policy"


def _asset_source_names(asset):
    out = []
    for src in asset.sources or []:
        s = norm_lower(src.source_name)
        if s and s not in out:
            out.append(s)
    return out


def _current_lifecycle_stage(asset):
    for row in asset.lifecycle_rows or []:
        if row.is_current:
            return norm_lower(row.stage)
    return ""


def _target_matches(policy, asset):
    target = policy.target_filters_json or {}
    if not isinstance(target, dict):
        return True

    def _arr(key):
        val = target.get(key)
        if isinstance(val, list):
            return [norm_lower(x) for x in val if norm_str(x)]
        if norm_str(val):
            return [norm_lower(val)]
        return []

    types = _arr("asset_types")
    if types and norm_lower(asset.asset_type) not in types:
        return False

    envs = _arr("environments")
    if envs and norm_lower(asset.environment) not in envs:
        return False

    locs = _arr("locations")
    if locs and norm_lower(asset.location) not in locs:
        return False

    statuses = _arr("statuses")
    if statuses and norm_lower(asset.status) not in statuses:
        return False

    return True


def _evaluate_policy_asset(policy, asset, now=None):
    now = now or datetime.now(timezone.utc)
    criteria = policy.criteria_json if isinstance(policy.criteria_json, dict) else {}
    ptype = norm_lower(policy.policy_type)
    severity = norm_lower(policy.severity) or "medium"

    if not _target_matches(policy, asset):
        return "not_applicable", 75, {"reason": "target_filter_mismatch", "severity": severity}

    try:
        if ptype == "required_tag":
            required = criteria.get("tag") or criteria.get("tags")
            tags_required = (
                [norm_lower(x) for x in required if norm_str(x)]
                if isinstance(required, list)
                else ([norm_lower(required)] if norm_str(required) else [])
            )
            asset_tags = {norm_lower(x) for x in (asset.tags_json or []) if norm_str(x)}
            missing = [x for x in tags_required if x not in asset_tags]
            if missing:
                return "fail", 0, {"reason": "missing_tag", "missing": missing, "severity": severity}
            return "pass", 100, {"reason": "tag_present", "severity": severity}

        if ptype == "required_source":
            required = criteria.get("source") or criteria.get("sources")
            required_sources = (
                [norm_lower(x) for x in required if norm_str(x)]
                if isinstance(required, list)
                else ([norm_lower(required)] if norm_str(required) else [])
            )
            have = set(_asset_source_names(asset))
            missing = [x for x in required_sources if x not in have]
            if missing:
                return "fail", 0, {"reason": "missing_source", "missing": missing, "severity": severity}
            return "pass", 100, {"reason": "source_present", "severity": severity}

        if ptype == "os_allowed":
            allowed = criteria.get("allowed_os") or criteria.get("allowed")
            allowed_list = (
                [norm_lower(x) for x in allowed if norm_str(x)]
                if isinstance(allowed, list)
                else ([norm_lower(allowed)] if norm_str(allowed) else [])
            )
            os_name = norm_lower(asset.os_name)
            if not os_name:
                return "fail", 0, {"reason": "missing_os", "severity": severity}
            if allowed_list and not any(a in os_name for a in allowed_list):
                return "fail", 0, {"reason": "os_not_allowed", "os_name": asset.os_name, "severity": severity}
            return "pass", 100, {"reason": "os_allowed", "os_name": asset.os_name, "severity": severity}

        if ptype == "max_days_since_seen":
            max_days = criteria.get("max_days") or criteria.get("max_days_since_seen")
            try:
                max_days = int(max_days)
            except Exception:
                max_days = 7
            if not asset.last_seen:
                return "fail", 0, {"reason": "never_seen", "severity": severity}
            age_days = max(0, int((now - asset.last_seen).total_seconds() // 86400))
            if age_days > max_days:
                return "fail", 0, {"reason": "stale_asset", "age_days": age_days, "max_days": max_days, "severity": severity}
            return "pass", 100, {"reason": "fresh_asset", "age_days": age_days, "max_days": max_days, "severity": severity}

        if ptype == "custom_field_required":
            field = norm_str(criteria.get("field"))
            val = (asset.custom_fields_json or {}).get(field) if field else None
            if field and norm_str(val):
                return "pass", 100, {"reason": "custom_field_present", "field": field, "severity": severity}
            return "fail", 0, {"reason": "custom_field_missing", "field": field, "severity": severity}

        if ptype == "custom_field_equals":
            field = norm_str(criteria.get("field"))
            expected = norm_str(criteria.get("value"))
            actual = norm_str((asset.custom_fields_json or {}).get(field))
            if field and actual == expected:
                return "pass", 100, {"reason": "custom_field_match", "field": field, "value": actual, "severity": severity}
            return "fail", 0, {"reason": "custom_field_mismatch", "field": field, "expected": expected, "actual": actual, "severity": severity}

        if ptype == "lifecycle_stage_in":
            stages = criteria.get("stages")
            stage_list = (
                [norm_lower(x) for x in stages if norm_str(x)]
                if isinstance(stages, list)
                else ([norm_lower(stages)] if norm_str(stages) else [])
            )
            stage = _current_lifecycle_stage(asset)
            if stage_list and stage not in stage_list:
                return "fail", 0, {"reason": "lifecycle_stage_mismatch", "stage": stage, "allowed": stage_list, "severity": severity}
            return "pass", 100, {"reason": "lifecycle_stage_allowed", "stage": stage, "severity": severity}

        return "error", 0, {"reason": "unknown_policy_type", "policy_type": ptype, "severity": severity}
    except Exception as ex:
        return "error", 0, {"reason": "evaluation_error", "message": str(ex), "severity": severity}


def run_compliance_evaluation(
    customer_id=None,
    policy_ids=None,
    asset_ids=None,
    triggered_by="system",
    limit_assets=2000,
):
    ensure_phase2_schema()
    now = datetime.now(timezone.utc)

    run = ItamComplianceRun(
        customer_id=customer_id,
        status="running",
        triggered_by=norm_str(triggered_by) or "system",
        started_at=now,
    )
    db.session.add(run)
    db.session.flush()

    summary = {
        "pass": 0,
        "fail": 0,
        "not_applicable": 0,
        "error": 0,
        "evaluations": 0,
    }

    try:
        policy_query = ItamCompliancePolicy.query.filter(ItamCompliancePolicy.enabled.is_(True))
        if customer_id is not None:
            policy_query = policy_query.filter(
                db.or_(
                    ItamCompliancePolicy.customer_id == customer_id,
                    ItamCompliancePolicy.customer_id.is_(None),
                )
            )
        if policy_ids:
            policy_query = policy_query.filter(ItamCompliancePolicy.id.in_(policy_ids))
        policies = policy_query.order_by(ItamCompliancePolicy.id.asc()).all()

        asset_query = ItamAsset.query
        if customer_id is not None:
            asset_query = asset_query.filter(ItamAsset.customer_id == customer_id)
        if asset_ids:
            asset_query = asset_query.filter(ItamAsset.id.in_(asset_ids))
        if int(limit_assets or 0) > 0:
            asset_query = asset_query.limit(int(limit_assets))
        assets = asset_query.order_by(ItamAsset.id.asc()).all()

        run.policy_count = len(policies)
        run.asset_count = len(assets)

        for policy in policies:
            for asset in assets:
                if policy.customer_id is not None and policy.customer_id != asset.customer_id:
                    continue

                status, score, details = _evaluate_policy_asset(policy, asset, now=now)
                finding = ItamComplianceFinding.query.filter(
                    ItamComplianceFinding.customer_id == asset.customer_id,
                    ItamComplianceFinding.policy_id == policy.id,
                    ItamComplianceFinding.asset_id == asset.id,
                ).first()
                if not finding:
                    finding = ItamComplianceFinding(
                        customer_id=asset.customer_id,
                        policy_id=policy.id,
                        asset_id=asset.id,
                    )
                    db.session.add(finding)

                finding.run_id = run.id
                finding.status = status
                finding.score = int(score or 0)
                finding.details_json = details or {}
                finding.evaluated_at = now
                finding.updated_at = now

                summary[status] = int(summary.get(status) or 0) + 1
                summary["evaluations"] += 1

        run.finding_count = int(summary["evaluations"])
        run.pass_count = int(summary["pass"])
        run.fail_count = int(summary["fail"])
        run.not_applicable_count = int(summary["not_applicable"])
        run.error_count = int(summary["error"])
        run.summary_json = summary
        run.status = "completed"
        run.ended_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception as ex:
        db.session.rollback()
        run.status = "failed"
        run.error_text = str(ex)
        run.summary_json = summary
        run.ended_at = datetime.now(timezone.utc)
        db.session.add(run)
        db.session.commit()
        raise

    return run, summary
