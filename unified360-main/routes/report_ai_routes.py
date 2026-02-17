from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from models.alert_rule import AlertRule
from models.alert_rule_state import AlertRuleState
from models.device_status_alert import DeviceStatusAlert
from models.report_ai import ReportNarrative, ReportSchedule
import security
from extensions import db

report_ai_bp = Blueprint("report_ai", __name__)


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


def _forbidden(permission):
    return jsonify({"ok": False, "error": f"Forbidden: Missing permission '{permission}'"}), 403


def _effective_customer_id(payload_customer_id):
    allowed = _allowed_customer_id()
    if allowed is None:
        return payload_customer_id
    return allowed


def _parse_date_utc(s, end=False):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).strip()).date()
        if end:
            return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
        return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    except Exception:
        return None


def _generate_narrative(customer_id, report_id, from_ts, to_ts, output_format):
    start = _parse_date_utc(from_ts, end=False)
    end = _parse_date_utc(to_ts, end=True)
    if not start or not end:
        return None

    dq = DeviceStatusAlert.query.filter(DeviceStatusAlert.updated_at >= start, DeviceStatusAlert.updated_at <= end)
    rq = (
        db.session.query(AlertRuleState, AlertRule)
        .join(AlertRule, AlertRule.id == AlertRuleState.rule_id)
        .filter(AlertRuleState.updated_at >= start, AlertRuleState.updated_at <= end)
    )
    if customer_id is not None:
        if hasattr(DeviceStatusAlert, "customer_id"):
            dq = dq.filter(DeviceStatusAlert.customer_id == customer_id)
        rq = rq.filter(AlertRule.customer_id == customer_id)

    device_events = dq.count()
    active_down = dq.filter(DeviceStatusAlert.is_active.is_(True), DeviceStatusAlert.last_status == "DOWN").count()
    rule_events = rq.count()
    active_rule = rq.filter(AlertRuleState.is_active.is_(True)).count()

    summary = (
        f"Report {report_id} summary for {from_ts} to {to_ts}: "
        f"{device_events} device status events and {rule_events} rule-state events observed. "
        f"Current active issues: {active_down} device-down and {active_rule} rule-triggered."
    )
    highlights = [
        {"label": "device_events", "value": device_events},
        {"label": "rule_state_events", "value": rule_events},
        {"label": "active_device_down", "value": active_down},
        {"label": "active_rule_triggered", "value": active_rule},
        {"label": "format", "value": output_format},
    ]
    return {"summary_text": summary, "highlights": highlights}


@report_ai_bp.get("/api/report-schedules")
@security.login_required_api
def report_schedules_list():
    if not _can("view_reports"):
        return _forbidden("view_reports")
    q = _scope_query(ReportSchedule.query, ReportSchedule).order_by(ReportSchedule.created_at.desc())
    return jsonify({"ok": True, "items": [x.to_dict() for x in q.all()]})


@report_ai_bp.post("/api/report-schedules")
@security.login_required_api
def report_schedules_create():
    if not _can("copilot.run_reports"):
        return _forbidden("copilot.run_reports")

    data = request.get_json(silent=True) or {}
    customer_id = _effective_customer_id(data.get("customer_id"))
    if not customer_id:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    name = (data.get("name") or "").strip()
    report_id = data.get("report_id")
    if not name or not report_id:
        return jsonify({"ok": False, "error": "name and report_id are required"}), 400

    u = _current_user()
    row = ReportSchedule(
        customer_id=customer_id,
        name=name,
        report_id=int(report_id),
        frequency=(data.get("frequency") or "weekly").strip().lower(),
        run_time=(data.get("run_time") or "09:00").strip(),
        timezone=(data.get("timezone") or "UTC").strip(),
        output_format=(data.get("output_format") or "pdf").strip().lower(),
        params_json=data.get("params") if isinstance(data.get("params"), dict) else {},
        recipients_json=data.get("recipients") if isinstance(data.get("recipients"), list) else [],
        is_active=bool(data.get("is_active", True)),
        created_by_user_id=(u.id if u else None),
        updated_by_user_id=(u.id if u else None),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()}), 201


@report_ai_bp.put("/api/report-schedules/<int:schedule_id>")
@security.login_required_api
def report_schedules_update(schedule_id):
    if not _can("copilot.run_reports"):
        return _forbidden("copilot.run_reports")

    row = _scope_query(ReportSchedule.query, ReportSchedule).filter(ReportSchedule.id == schedule_id).first_or_404()
    data = request.get_json(silent=True) or {}

    if "name" in data:
        row.name = (data.get("name") or "").strip() or row.name
    if "frequency" in data:
        row.frequency = (data.get("frequency") or "weekly").strip().lower()
    if "run_time" in data:
        row.run_time = (data.get("run_time") or "09:00").strip()
    if "timezone" in data:
        row.timezone = (data.get("timezone") or "UTC").strip()
    if "output_format" in data:
        row.output_format = (data.get("output_format") or "pdf").strip().lower()
    if "params" in data and isinstance(data.get("params"), dict):
        row.params_json = data.get("params")
    if "recipients" in data and isinstance(data.get("recipients"), list):
        row.recipients_json = data.get("recipients")
    if "is_active" in data:
        row.is_active = bool(data.get("is_active"))

    u = _current_user()
    row.updated_by_user_id = u.id if u else None
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@report_ai_bp.delete("/api/report-schedules/<int:schedule_id>")
@security.login_required_api
def report_schedules_delete(schedule_id):
    if not _can("copilot.run_reports"):
        return _forbidden("copilot.run_reports")
    row = _scope_query(ReportSchedule.query, ReportSchedule).filter(ReportSchedule.id == schedule_id).first_or_404()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@report_ai_bp.post("/api/reports/narrate")
@security.login_required_api
def report_narrate():
    if not _can("view_reports"):
        return _forbidden("view_reports")

    data = request.get_json(silent=True) or {}
    report_id = data.get("report_id")
    from_ts = (data.get("from") or "").strip()
    to_ts = (data.get("to") or "").strip()
    output_format = (data.get("format") or "pdf").strip().lower()
    customer_id = _effective_customer_id(data.get("customer_id"))

    if not report_id or not from_ts or not to_ts:
        return jsonify({"ok": False, "error": "report_id, from, to are required"}), 400
    if customer_id is None:
        return jsonify({"ok": False, "error": "customer_id is required"}), 400

    generated = _generate_narrative(customer_id, int(report_id), from_ts, to_ts, output_format)
    if not generated:
        return jsonify({"ok": False, "error": "invalid date range"}), 400

    u = _current_user()
    row = ReportNarrative(
        customer_id=customer_id,
        report_id=int(report_id),
        from_ts=from_ts,
        to_ts=to_ts,
        output_format=output_format,
        summary_text=generated["summary_text"],
        highlights_json=generated["highlights"],
        generated_by_user_id=(u.id if u else None),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "item": row.to_dict()})


@report_ai_bp.get("/api/reports/narratives")
@security.login_required_api
def report_narratives_list():
    if not _can("view_reports"):
        return _forbidden("view_reports")

    q = _scope_query(ReportNarrative.query, ReportNarrative).order_by(ReportNarrative.created_at.desc())
    report_id = request.args.get("report_id", type=int)
    if report_id:
        q = q.filter(ReportNarrative.report_id == report_id)
    return jsonify({"ok": True, "items": [x.to_dict() for x in q.limit(200).all()]})
