from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from extensions import db
from models.remediation import RemediationAction, Runbook
import security

remediation_bp = Blueprint("remediation", __name__)


def _current_user():
    return security.get_current_user()


def _allowed_customer_id():
    return security.get_allowed_customer_id(_current_user())


def _can(permission_code):
    u = _current_user()
    if not u:
        return False
    return security.has_permission(u, permission_code)


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


def _forbidden(permission):
    return jsonify({"ok": False, "error": f"Forbidden: Missing permission '{permission}'"}), 403


def _pick_runbook(customer_id, source_type):
    st = (source_type or "").strip().lower()
    q = (
        Runbook.query.filter(Runbook.customer_id == customer_id, Runbook.is_active.is_(True))
        .order_by(Runbook.risk_level.asc(), Runbook.id.asc())
    )
    exact = q.filter(Runbook.trigger_type.ilike(st)).first()
    if exact:
        return exact
    wildcard = q.filter(Runbook.trigger_type.in_(["any", "*"])).first()
    return wildcard


@remediation_bp.get("/api/remediation/runbooks")
@security.login_required_api
def runbooks_list():
    if not _can("copilot.use"):
        return _forbidden("copilot.use")

    q = _scope_query(Runbook.query, Runbook).order_by(Runbook.created_at.desc())
    items = [x.to_dict() for x in q.all()]
    return jsonify({"ok": True, "items": items})


@remediation_bp.post("/api/remediation/runbooks")
@security.login_required_api
def runbooks_create():
    if not _can("copilot.remediate"):
        return _forbidden("copilot.remediate")

    data = request.get_json(silent=True) or {}
    customer_id = _effective_customer_id(data.get("customer_id"))
    if not customer_id:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    name = (data.get("name") or "").strip()
    trigger_type = (data.get("trigger_type") or "").strip().lower()
    steps = data.get("steps")
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400
    if not trigger_type:
        return jsonify({"ok": False, "error": "trigger_type is required"}), 400
    if not isinstance(steps, list) or not steps:
        return jsonify({"ok": False, "error": "steps must be a non-empty list"}), 400

    u = _current_user()
    row = Runbook(
        customer_id=customer_id,
        name=name,
        trigger_type=trigger_type,
        description=(data.get("description") or "").strip() or None,
        steps_json=steps,
        risk_level=(data.get("risk_level") or "medium").strip().lower(),
        requires_approval=bool(data.get("requires_approval", True)),
        allowed_roles_json=data.get("allowed_roles") if isinstance(data.get("allowed_roles"), list) else [],
        is_active=bool(data.get("is_active", True)),
        created_by_user_id=(u.id if u else None),
        updated_by_user_id=(u.id if u else None),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()}), 201


@remediation_bp.put("/api/remediation/runbooks/<int:runbook_id>")
@security.login_required_api
def runbooks_update(runbook_id):
    if not _can("copilot.remediate"):
        return _forbidden("copilot.remediate")

    row = _scope_query(Runbook.query, Runbook).filter(Runbook.id == runbook_id).first_or_404()
    data = request.get_json(silent=True) or {}
    if "name" in data:
        row.name = (data.get("name") or "").strip() or row.name
    if "trigger_type" in data:
        row.trigger_type = (data.get("trigger_type") or "").strip().lower() or row.trigger_type
    if "description" in data:
        row.description = (data.get("description") or "").strip() or None
    if "steps" in data:
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            return jsonify({"ok": False, "error": "steps must be a non-empty list"}), 400
        row.steps_json = steps
    if "risk_level" in data:
        row.risk_level = (data.get("risk_level") or "medium").strip().lower()
    if "requires_approval" in data:
        row.requires_approval = bool(data.get("requires_approval"))
    if "allowed_roles" in data and isinstance(data.get("allowed_roles"), list):
        row.allowed_roles_json = data.get("allowed_roles")
    if "is_active" in data:
        row.is_active = bool(data.get("is_active"))

    u = _current_user()
    row.updated_by_user_id = u.id if u else None
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@remediation_bp.post("/api/remediation/suggest")
@security.login_required_api
def remediation_suggest():
    if not _can("copilot.use"):
        return _forbidden("copilot.use")

    data = request.get_json(silent=True) or {}
    source_type = (data.get("source_type") or "").strip().lower()
    source_ref = (data.get("source_ref") or "").strip()
    summary = (data.get("summary") or "").strip()
    if not source_type:
        return jsonify({"ok": False, "error": "source_type is required"}), 400

    customer_id = _effective_customer_id(data.get("customer_id"))
    if not customer_id:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    runbook = None
    runbook_id = data.get("runbook_id")
    if runbook_id:
        runbook = _scope_query(Runbook.query, Runbook).filter(Runbook.id == runbook_id).first()
        if not runbook:
            return jsonify({"ok": False, "error": "invalid runbook_id"}), 404
    else:
        runbook = _pick_runbook(customer_id, source_type)

    steps = (runbook.steps_json if runbook else [])
    requires_approval = (runbook.requires_approval if runbook else True)
    if not steps:
        steps = [
            {"step": "Validate alert context and impacted scope"},
            {"step": "Run standard diagnostics and collect telemetry"},
            {"step": "Escalate to manual operations if no safe automation exists"},
        ]

    u = _current_user()
    action = RemediationAction(
        customer_id=customer_id,
        runbook_id=(runbook.id if runbook else None),
        source_type=source_type,
        source_ref=source_ref or None,
        summary=summary or f"Remediation suggestion for {source_type}",
        proposed_steps_json=steps,
        status="proposed",
        requires_approval=requires_approval,
        requested_by_user_id=(u.id if u else None),
    )
    db.session.add(action)
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "item": action.to_dict(),
            "runbook": runbook.to_dict() if runbook else None,
        }
    ), 201


@remediation_bp.get("/api/remediation/actions")
@security.login_required_api
def remediation_actions_list():
    if not _can("copilot.use"):
        return _forbidden("copilot.use")

    status = (request.args.get("status") or "").strip().lower()
    q = _scope_query(RemediationAction.query, RemediationAction).order_by(RemediationAction.created_at.desc())
    if status:
        q = q.filter(RemediationAction.status == status)
    items = [x.to_dict() for x in q.limit(200).all()]
    return jsonify({"ok": True, "items": items})


@remediation_bp.post("/api/remediation/actions/<int:action_id>/approve")
@security.login_required_api
def remediation_action_approve(action_id):
    if not _can("copilot.remediate"):
        return _forbidden("copilot.remediate")

    row = _scope_query(RemediationAction.query, RemediationAction).filter(RemediationAction.id == action_id).first_or_404()
    if row.status not in ("proposed", "approved"):
        return jsonify({"ok": False, "error": f"cannot approve from status {row.status}"}), 400

    u = _current_user()
    row.status = "approved"
    row.approved_by_user_id = u.id if u else None
    row.approved_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@remediation_bp.post("/api/remediation/actions/<int:action_id>/execute")
@security.login_required_api
def remediation_action_execute(action_id):
    if not _can("copilot.remediate"):
        return _forbidden("copilot.remediate")

    row = _scope_query(RemediationAction.query, RemediationAction).filter(RemediationAction.id == action_id).first_or_404()
    if row.requires_approval and row.status != "approved":
        return jsonify({"ok": False, "error": "action requires approval before execution"}), 400
    if row.status not in ("approved", "proposed", "failed"):
        return jsonify({"ok": False, "error": f"cannot execute from status {row.status}"}), 400

    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", True))

    u = _current_user()
    now = datetime.now(timezone.utc)
    row.executed_by_user_id = u.id if u else None
    row.executed_at = now

    # Placeholder executor: keep human-in-the-loop safe behavior by default.
    row.status = "executed"
    row.output_json = {
        "mode": "dry_run" if dry_run else "manual_placeholder",
        "message": "Execution scaffold complete. Integrate real runbook executors in next phase.",
        "executed_at": now.isoformat().replace("+00:00", "Z"),
    }
    db.session.commit()

    return jsonify({"ok": True, "item": row.to_dict()})
