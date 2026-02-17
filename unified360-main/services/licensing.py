from __future__ import annotations

from datetime import datetime, timedelta
import os
import sqlite3
import time
from typing import Dict, Tuple

from extensions import db
from models.customer import Customer
from models.license import License, LicenseItem
from models.snmp import SnmpConfig
from models.idrac import IdracConfig
from models.ilo import IloConfig
from models.ping import PingConfig
from models.port_monitor import PortMonitor
from models.url_monitor import UrlMonitor
from models.link_monitor import LinkMonitor
from models.sqlserver_monitor import SqlServerMonitor
from models.oracle_db_monitor import OracleDbMonitor


# ------------------------------------------------------------
# Monitoring Types (all must be licensed)
# ------------------------------------------------------------
MONITORING_TYPES = [
    "server",
    "snmp",
    "idrac",
    "ilo",
    "ping",
    "port",
    "url",
    "link",
    "sqlserver",
    "oracle",
]

TYPE_TO_MODEL = {
    "snmp": SnmpConfig,
    "idrac": IdracConfig,
    "ilo": IloConfig,
    "ping": PingConfig,
    "port": PortMonitor,
    "url": UrlMonitor,
    "link": LinkMonitor,
    "sqlserver": SqlServerMonitor,
    "oracle": OracleDbMonitor,
}

USAGE_CACHE_TTL = 60  # seconds
_USAGE_CACHE: Dict[int, Tuple[float, Dict[str, int]]] = {}


def _get_cache_db_path() -> str:
    return os.environ.get(
        "AUTOINTER_CACHE_DB",
        "/usr/local/autointelli/opsduty-server/.servers_cache.db",
    )


def _server_usage(customer_id: int) -> int:
    customer = Customer.query.get(customer_id)
    if not customer:
        return 0

    path = _get_cache_db_path()
    if not os.path.exists(path):
        return 0

    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(DISTINCT instance)
            FROM servers_cache
            WHERE lower(customer_name) = lower(?)
            """,
            (customer.name,),
        )
        row = cur.fetchone()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _model_usage(model_cls, customer_id: int) -> int:
    return int(model_cls.query.filter_by(customer_id=customer_id).count())


def get_usage(customer_id: int) -> Dict[str, int]:
    now = time.time()
    cached = _USAGE_CACHE.get(customer_id)
    if cached and (now - cached[0]) < USAGE_CACHE_TTL:
        return cached[1]

    usage: Dict[str, int] = {"server": _server_usage(customer_id)}

    for mtype, model_cls in TYPE_TO_MODEL.items():
        usage[mtype] = _model_usage(model_cls, customer_id)

    _USAGE_CACHE[customer_id] = (now, usage)
    return usage


def get_license(customer_id: int) -> License | None:
    return (
        License.query.filter_by(customer_id=customer_id)
        .order_by(License.expires_at.desc())
        .first()
    )


def license_status(license_obj: License | None) -> Tuple[str, Dict[str, str | None]]:
    if not license_obj:
        return "missing", {
            "expires_at": None,
            "grace_until": None,
        }

    now = datetime.utcnow()
    expires_at = license_obj.expires_at
    grace_until = expires_at + timedelta(days=license_obj.grace_days or 0)

    if now <= expires_at:
        status = "active"
    elif now <= grace_until:
        status = "grace"
    else:
        status = "expired"

    return status, {
        "expires_at": expires_at.isoformat() if expires_at else None,
        "grace_until": grace_until.isoformat() if grace_until else None,
    }


def get_limits(license_obj: License | None) -> Dict[str, int]:
    if not license_obj:
        return {}
    return {
        item.monitoring_type: int(item.max_count or 0)
        for item in (license_obj.items or [])
    }


def get_license_snapshot(customer_id: int) -> Dict:
    lic = get_license(customer_id)
    status, time_meta = license_status(lic)
    limits = get_limits(lic)
    usage = get_usage(customer_id)

    remaining = {}
    for mtype in MONITORING_TYPES:
        limit = limits.get(mtype)
        if limit is None:
            remaining[mtype] = None
        else:
            remaining[mtype] = max(0, limit - usage.get(mtype, 0))

    return {
        "status": status,
        "license_id": lic.id if lic else None,
        "customer_id": customer_id,
        "limits": limits,
        "usage": usage,
        "remaining": remaining,
        **time_meta,
    }


def can_add_monitor(customer_id: int, monitoring_type: str, delta: int = 1) -> Tuple[bool, Dict]:
    snap = get_license_snapshot(customer_id)
    status = snap["status"]

    if status in {"missing", "expired"}:
        snap["message"] = "License expired or missing."
        return False, snap

    limits = snap.get("limits") or {}
    if monitoring_type not in limits:
        snap["message"] = f"No license for monitoring type '{monitoring_type}'."
        return False, snap

    limit = limits.get(monitoring_type, 0)
    usage = snap.get("usage", {}).get(monitoring_type, 0)

    if usage + delta > limit:
        snap["message"] = f"License limit exceeded for '{monitoring_type}'."
        return False, snap

    if status == "grace":
        snap["message"] = "License in grace period."
    else:
        snap["message"] = "License OK."

    return True, snap
