from datetime import datetime, timedelta, timezone

from extensions import db
from models.itam import ItamDiscoveryPolicy
from services.itam.ingest import DEFAULT_SOURCES, SUPPORTED_SOURCES, run_discovery


def _normalize_sources(items):
    sources = []
    for x in items or []:
        s = str(x or "").strip().lower()
        if s and s in SUPPORTED_SOURCES and s not in sources:
            sources.append(s)
    if not sources:
        sources = list(DEFAULT_SOURCES)
    return sources


def get_or_create_policy():
    row = ItamDiscoveryPolicy.query.order_by(ItamDiscoveryPolicy.id.asc()).first()
    if row:
        if not row.sources_json:
            row.sources_json = list(DEFAULT_SOURCES)
            db.session.commit()
        return row

    row = ItamDiscoveryPolicy(
        enabled=False,
        interval_minutes=60,
        sources_json=list(DEFAULT_SOURCES),
        target_customer_id=None,
        last_run_status="never",
        last_run_summary_json={},
    )
    db.session.add(row)
    db.session.commit()
    return row


def update_policy(
    enabled=None,
    interval_minutes=None,
    sources=None,
    target_customer_id=None,
    set_target_customer=False,
):
    row = get_or_create_policy()

    if enabled is not None:
        row.enabled = bool(enabled)
    if interval_minutes is not None:
        row.interval_minutes = max(5, min(int(interval_minutes), 1440))
    if sources is not None:
        row.sources_json = _normalize_sources(sources)
    if set_target_customer:
        row.target_customer_id = target_customer_id

    row.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return row


def run_policy_now():
    row = get_or_create_policy()
    sources = _normalize_sources(row.sources_json)
    started = datetime.now(timezone.utc)

    row.last_run_started_at = started
    row.last_run_status = "running"
    row.last_error_text = None
    db.session.commit()

    try:
        run, summary = run_discovery(
            sources=sources,
            customer_id=row.target_customer_id,
            cloud_assets=None,
        )
        row.last_run_ended_at = datetime.now(timezone.utc)
        row.last_run_status = run.status
        row.last_run_summary_json = summary
        row.last_error_text = run.error_text or None
        db.session.commit()
        return {"ok": True, "ran": True, "policy": row, "run": run, "summary": summary}
    except Exception as ex:
        row.last_run_ended_at = datetime.now(timezone.utc)
        row.last_run_status = "failed"
        row.last_error_text = str(ex)
        row.last_run_summary_json = {}
        db.session.commit()
        return {"ok": False, "ran": True, "policy": row, "error": str(ex)}


def run_scheduler_tick(now=None):
    now = now or datetime.now(timezone.utc)
    row = get_or_create_policy()

    if not row.enabled:
        return {"ok": True, "ran": False, "reason": "disabled", "policy": row}

    # If a run is currently marked running and recent, skip this tick.
    if row.last_run_status == "running" and row.last_run_started_at:
        if now - row.last_run_started_at < timedelta(minutes=max(5, row.interval_minutes or 60)):
            return {"ok": True, "ran": False, "reason": "already_running", "policy": row}

    last_at = row.last_run_ended_at or row.last_run_started_at
    if last_at:
        wait_for = timedelta(minutes=max(5, row.interval_minutes or 60))
        if now - last_at < wait_for:
            return {"ok": True, "ran": False, "reason": "not_due", "policy": row}

    return run_policy_now()
