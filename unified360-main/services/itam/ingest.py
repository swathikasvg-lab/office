from datetime import datetime, timezone

from extensions import db
from models.itam import ItamDiscoveryRun
from services.itam.connectors import (
    discover_from_aws,
    discover_from_azure,
    discover_from_cloud_payload,
    discover_from_desktop_cache,
    discover_from_gcp,
    discover_from_ot_bacnet,
    discover_from_ot_modbus,
    discover_from_ot_opcua,
    discover_from_ot_payload,
    discover_from_ot_seed,
    discover_from_servers_cache,
    discover_from_snmp_configs,
)
from services.itam.reconcile import upsert_asset_from_record
from services.itam.schema import ensure_phase2_schema


DEFAULT_SOURCES = ("servers_cache", "desktop_cache", "snmp")
SUPPORTED_SOURCES = {
    "servers_cache",
    "desktop_cache",
    "snmp",
    "cloud_manual",
    "cloud_aws",
    "cloud_azure",
    "cloud_gcp",
    "ot_seed",
    "ot_manual",
    "ot_modbus",
    "ot_bacnet",
    "ot_opcua",
}


def _filtered_cloud_accounts(items, provider):
    if not isinstance(items, list) or not items:
        return None
    out = []
    p = str(provider or "").strip().lower()
    for row in items:
        if not isinstance(row, dict):
            continue
        rp = str(row.get("provider") or "").strip().lower()
        if not rp or rp == p:
            out.append(row)
    return out


def _discover_records_for_source(
    source_name,
    customer_id=None,
    cloud_assets=None,
    cloud_accounts=None,
    ot_assets=None,
):
    if source_name == "servers_cache":
        return discover_from_servers_cache(customer_id=customer_id)
    if source_name == "desktop_cache":
        return discover_from_desktop_cache(customer_id=customer_id)
    if source_name == "snmp":
        return discover_from_snmp_configs(customer_id=customer_id)
    if source_name == "cloud_manual":
        if customer_id is None:
            return []
        return discover_from_cloud_payload(customer_id=customer_id, assets=cloud_assets or [])
    if source_name == "cloud_aws":
        return discover_from_aws(
            customer_id=customer_id,
            accounts=_filtered_cloud_accounts(cloud_accounts, "aws"),
        )
    if source_name == "cloud_azure":
        return discover_from_azure(
            customer_id=customer_id,
            subscriptions=_filtered_cloud_accounts(cloud_accounts, "azure"),
        )
    if source_name == "cloud_gcp":
        return discover_from_gcp(
            customer_id=customer_id,
            projects=_filtered_cloud_accounts(cloud_accounts, "gcp"),
        )
    if source_name == "ot_seed":
        return discover_from_ot_seed(customer_id=customer_id)
    if source_name == "ot_manual":
        if customer_id is None:
            return []
        return discover_from_ot_payload(customer_id=customer_id, assets=ot_assets or [])
    if source_name == "ot_modbus":
        return discover_from_ot_modbus(customer_id=customer_id)
    if source_name == "ot_bacnet":
        return discover_from_ot_bacnet(customer_id=customer_id)
    if source_name == "ot_opcua":
        return discover_from_ot_opcua(customer_id=customer_id)
    return []


def run_discovery(
    sources=None,
    customer_id=None,
    cloud_assets=None,
    cloud_accounts=None,
    ot_assets=None,
):
    ensure_phase2_schema()

    source_list = [s for s in (sources or DEFAULT_SOURCES) if s in SUPPORTED_SOURCES]
    if not source_list:
        source_list = list(DEFAULT_SOURCES)

    run = ItamDiscoveryRun(
        customer_id=customer_id,
        source_name=",".join(source_list),
        status="running",
        started_at=datetime.now(timezone.utc),
        stats_json={},
    )
    db.session.add(run)
    db.session.flush()

    summary = {
        "sources": {},
        "assets_created": 0,
        "assets_updated": 0,
        "records_seen": 0,
        "records_skipped": 0,
        "errors": [],
    }

    try:
        for source_name in source_list:
            per = {
                "records_seen": 0,
                "assets_created": 0,
                "assets_updated": 0,
                "records_skipped": 0,
                "errors": 0,
            }
            summary["sources"][source_name] = per

            records = _discover_records_for_source(
                source_name=source_name,
                customer_id=customer_id,
                cloud_assets=cloud_assets,
                cloud_accounts=cloud_accounts,
                ot_assets=ot_assets,
            )

            for item in records:
                per["records_seen"] += 1
                summary["records_seen"] += 1
                cid = item.get("customer_id")
                if cid is None:
                    per["records_skipped"] += 1
                    summary["records_skipped"] += 1
                    continue

                try:
                    _, created = upsert_asset_from_record(
                        customer_id=cid,
                        source_name=source_name,
                        source_key=item.get("source_key") or f"{source_name}:{per['records_seen']}",
                        record=item.get("record") or {},
                        discovered_at=item.get("discovered_at"),
                        confidence=item.get("confidence") or 80,
                    )
                    if created:
                        per["assets_created"] += 1
                        summary["assets_created"] += 1
                    else:
                        per["assets_updated"] += 1
                        summary["assets_updated"] += 1
                except Exception as ex:
                    per["errors"] += 1
                    summary["errors"].append(f"{source_name}: {str(ex)}")

        run.status = "completed" if not summary["errors"] else "completed_with_errors"
        run.stats_json = summary
        run.ended_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception as ex:
        db.session.rollback()
        run.status = "failed"
        run.error_text = str(ex)
        run.stats_json = summary
        run.ended_at = datetime.now(timezone.utc)
        db.session.add(run)
        db.session.commit()
        raise

    return run, summary
