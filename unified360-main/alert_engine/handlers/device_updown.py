"""
Device Up / Down Monitoring
Production-grade, rule-driven, customer-aware
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple
import logging
import requests
from flask import current_app

from extensions import db
from models.device_status_alert import DeviceStatusAlert
from models.smtp import SmtpConfig
from models.contact import ContactGroup
from models.device_updown_rule import DeviceUpDownRule


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
logger = logging.getLogger("alert_engine.device_updown")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------
# Time Helpers
# ---------------------------------------------------------------------

def ensure_utc(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(dt: datetime | None) -> str:
    if not dt:
        return "N/A"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def normalize_instance(v: str | None) -> str:
    if not v:
        return "unknown"
    v = v.strip()
    if "://" in v:
        v = v.split("://", 1)[1]
    if ":" in v:
        v = v.split(":", 1)[0]
    return v


def format_downtime(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------
# SMTP / Contacts
# ---------------------------------------------------------------------
def get_smtp_config() -> SmtpConfig | None:
    return SmtpConfig.query.first()


def get_recipients(group_id: int) -> List[str]:
    group = ContactGroup.query.get(group_id)
    if not group:
        logger.warning("ContactGroup %s not found", group_id)
        return []
    return sorted({c.email for c in group.contacts if c.email})


def send_email(subject: str, html: str, recipients: List[str]) -> None:
    if not recipients:
        logger.warning("No recipients, skipping email: %s", subject)
        return

    cfg = get_smtp_config()
    if not cfg:
        logger.error("SMTP config missing, cannot send email")
        return

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    try:
        if cfg.security.upper() == "SSL":
            server = smtplib.SMTP_SSL(cfg.host, cfg.port)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port)

        server.ehlo()
        if cfg.security.upper() == "TLS":
            server.starttls()

        if cfg.username and cfg.password:
            server.login(cfg.username, cfg.password)

        server.sendmail(cfg.sender, recipients, msg.as_string())
        server.quit()
        logger.info("Email sent: %s → %s", subject, recipients)

    except Exception:
        logger.exception("SMTP send failed")


# ---------------------------------------------------------------------
# Email Template
# ---------------------------------------------------------------------
def build_email(
    *,
    event: str,
    source: str,
    device: str,
    stale_seconds: int,
    down_since: datetime | None,
    now: datetime,
    downtime: float,
) -> Tuple[str, str]:

    kind = "Server" if source == "server" else "Network Device"
    status = "DOWN" if event == "down" else "UP"

    subject = (
        f"{kind} Down Alert: {device}"
        if event == "down"
        else f"{kind} Recovery: {device} is UP"
    )

    summary = (
        f"{kind} {device} has not reported metrics for over {stale_seconds} seconds."
        if event == "down"
        else f"{kind} {device} has recovered and is reporting again."
    )

    html = f"""
    <div style="font-family:Arial;font-size:14px">
      <h2>{subject}</h2>
      <p><b>Summary:</b> {summary}</p>
      <table cellpadding="4">
        <tr><td><b>Source</b></td><td>{kind}</td></tr>
        <tr><td><b>Device</b></td><td>{device}</td></tr>
        <tr><td><b>Status</b></td><td>{status}</td></tr>
        <tr><td><b>Alert Time</b></td><td>{fmt_ts(now)}</td></tr>
        <tr><td><b>Down Since</b></td><td>{fmt_ts(down_since)}</td></tr>
        <tr><td><b>Downtime</b></td><td>{format_downtime(downtime)}</td></tr>
      </table>
      <p>Regards,<br>Autointelli</p>
    </div>
    """
    return subject, html


# ---------------------------------------------------------------------
# Metrics Queries
# ---------------------------------------------------------------------
def prom_query(q: str) -> List[dict]:
    prom = current_app.config["PROMETHEUS_URL"].rstrip("/")
    resp = requests.get(f"{prom}/api/v1/query", params={"query": q}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("result", [])


def get_snmp_last_seen() -> Dict[str, float]:
    influx_url = current_app.config["INFLUXDB_URL"]
    db_name = current_app.config["INFLUXDB_DB"]

    query = """
    SELECT last(sysUpTime)
    FROM "snmpdevice"
    GROUP BY "hostname"
    """

    try:
        resp = requests.get(
            influx_url,
            params={"db": db_name, "q": query},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("InfluxDB SNMP query failed")
        return {}

    seen = {}
    series = data.get("results", [{}])[0].get("series", [])
    for s in series:
        host = s.get("tags", {}).get("hostname")
        ts = s["values"][-1][0]
        try:
            seen[host] = datetime.fromisoformat(
                ts.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            logger.warning("Invalid SNMP timestamp for %s", host)

    return seen


def _get_idrac_last_seen_for_customer(customer_name: str | None) -> Dict[str, float]:
    influx_url = current_app.config["INFLUXDB_URL"]
    db_name = current_app.config["INFLUXDB_DB"]

    where = ""
    if customer_name:
        where = f"WHERE customer_name = '{customer_name}'"

    query = f'''
        SELECT last("system-uptime") 
        FROM "idrac-hosts"
        {where}
        GROUP BY "agent_host"
    '''

    params = {"db": db_name, "q": query}

    try:
        resp = requests.get(influx_url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[DeviceUpDown] iDRAC Influx error: {exc}")
        return {}

    result = {}
    for s in data.get("results", [{}])[0].get("series", []):
        host = s.get("tags", {}).get("agent_host")
        ts = s["values"][-1][0]
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        result[host] = dt.timestamp()

    return result



def _get_ilo_last_seen_for_customer(customer_name: str | None) -> dict[str, float]:
    """
    Uses DISTINCT(agent_host) from ilo_snmp seen in the last 24h.
    Since DISTINCT does not return a real timestamp, devices are
    treated as 'seen now' (fresh within 24h window).
    """

    influx_url = current_app.config["INFLUXDB_URL"]
    db_name = current_app.config["INFLUXDB_DB"]

    where_clauses = ['time >= now() - 24h']
    if customer_name:
        where_clauses.append(f"customer_name = '{customer_name}'")

    where = "WHERE " + " AND ".join(where_clauses)

    query = f'''
        SELECT DISTINCT("agent_host")
        FROM (select * from "ilo_snmp" {where})
    '''

    #print(query)

    params = {"db": db_name, "q": query}

    try:
        resp = requests.get(influx_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[DeviceUpDown] iLO Influx error: {exc}")
        return {}

    result: dict[str, float] = {}
    now_ts = datetime.now(timezone.utc).timestamp()

    series = data.get("results", [{}])[0].get("series", [])
    if not series:
        return result

    for row in series[0].get("values", []):
        # row format: [ "1970-01-01T00:00:00Z", "<agent_host>" ]
        agent_host = row[1]
        result[agent_host] = now_ts

    return result



def _get_server_last_seen_for_customer(customer_name: str | None) -> Dict[str, float]:
    label_filter = ""
    if customer_name:
        label_filter = f'CustomerName="{customer_name}"'

    query = f"""
    max by(instance) (
        timestamp(node_cpu_seconds_total{{{label_filter}}})
        or
        timestamp(windows_cpu_time_total{{{label_filter}}})
    )
    """

    results = prom_query(query)
    ts_map = {}

    for r in results:
        inst = normalize_instance(r["metric"].get("instance"))
        ts_map[inst] = float(r["value"][1])

    return ts_map



def _get_snmp_last_seen_for_customer(customer_name: str | None) -> Dict[str, float]:
    influx_url = current_app.config["INFLUXDB_URL"]
    db_name = current_app.config["INFLUXDB_DB"]

    where = ""
    if customer_name:
        where = f"WHERE customer_name = '{customer_name}'"

    query = f'''
        SELECT last(sysUpTime) AS uptime
        FROM "snmpdevice"
        {where}
        GROUP BY "hostname"
    '''

    params = {"db": db_name, "q": query}

    try:
        resp = requests.get(influx_url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[DeviceUpDown] Influx error: {exc}")
        return {}

    result = {}
    for s in data["results"][0].get("series", []):
        hostname = s.get("tags", {}).get("hostname")
        ts = s["values"][-1][0]
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        result[hostname] = dt.timestamp()

    return result


def get_server_last_seen() -> Dict[str, float]:
    query = """
    max by(instance) (
        timestamp(node_cpu_seconds_total)
        or timestamp(windows_cpu_time_total)
    )
    """
    seen = {}
    for r in prom_query(query):
        inst = normalize_instance(r["metric"].get("instance"))
        seen[inst] = float(r["value"][1])
    return seen


# ---------------------------------------------------------------------
# Rule Resolution
# ---------------------------------------------------------------------
def resolve_contact_groups(source: str, device: str) -> List[int]:
    rules = (
        DeviceUpDownRule.query
        .filter_by(source=source, device=device, is_enabled=True)
        .order_by(DeviceUpDownRule.updated_at.desc())
        .all()
    )

    if rules:
        return list({r.contact_group_id for r in rules})

    fallback = current_app.config.get("DEVICE_UPDOWN_DEFAULT_CONTACT_GROUP_ID")
    if fallback:
        logger.warning("Using fallback contact group for %s:%s", source, device)
        return [int(fallback)]

    return []


# ---------------------------------------------------------------------
# Core Evaluation
# ---------------------------------------------------------------------
def process_device(
    *,
    source: str,
    device: str,
    last_seen_ts: float | None,
    stale_seconds: int,
    now: datetime,
) -> None:

    delay = (now.timestamp() - last_seen_ts) if last_seen_ts else stale_seconds + 1
    status = "UP" if delay <= stale_seconds else "DOWN"

    state = DeviceStatusAlert.query.filter_by(
        source=source, device=device
    ).first()

    logger.debug(
        "Evaluate %s:%s last_seen=%s delay=%ss status=%s",
        source, device, last_seen_ts, int(delay), status
    )

    if not state:
        state = DeviceStatusAlert(
            source=source,
            device=device,
            last_status=status,
            is_active=(status == "DOWN"),
            last_change=now,
            down_since=now if status == "DOWN" else None,
        )
        db.session.add(state)

        if status == "DOWN":
            for gid in resolve_contact_groups(source, device):
                subj, body = build_email(
                    event="down",
                    source=source,
                    device=device,
                    stale_seconds=stale_seconds,
                    down_since=now,
                    now=now,
                    downtime=delay,
                )
                send_email(subj, body, get_recipients(gid))
        return

    if state.last_status == status:
        return

    prev = state.last_status
    state.last_status = status
    state.last_change = now

    logger.info(
        "State change %s:%s %s → %s",
        source, device, prev, status
    )

    for gid in resolve_contact_groups(source, device):
        recipients = get_recipients(gid)

        if prev == "UP" and status == "DOWN":
            state.is_active = True
            state.down_since = now
            subj, body = build_email(
                event="down",
                source=source,
                device=device,
                stale_seconds=stale_seconds,
                down_since=now,
                now=now,
                downtime=delay,
            )
            send_email(subj, body, recipients)

        elif prev == "DOWN" and status == "UP":
            state.is_active = False
            down_since = ensure_utc(state.down_since)
            outage = (now - down_since).total_seconds() if down_since else 0

            state.total_downtime_sec += int(outage)
            state.last_recovered = now
            state.down_since = None
            subj, body = build_email(
                event="recovery",
                source=source,
                device=device,
                stale_seconds=stale_seconds,
                down_since=None,
                now=now,
                downtime=outage,
            )
            send_email(subj, body, recipients)


# ---------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------
def run_device_updown_cycle() -> None:
    now = utcnow()
    stale_seconds = int(
        current_app.config.get("DEVICE_STALE_SECONDS", 300)
    )

    logger.info("DeviceUpDown cycle start @ %s", fmt_ts(now))

    # -------------------------------------------------
    # SNMP (rule-driven)
    # -------------------------------------------------
    snmp_seen = get_snmp_last_seen()

    snmp_rules = DeviceUpDownRule.query.filter_by(
        source="snmp",
        is_enabled=True
    ).all()

    logger.info(
        "Evaluating %d SNMP device rules",
        len(snmp_rules),
    )

    for rule in snmp_rules:
        last_ts = snmp_seen.get(rule.device)
        process_device(
            source="snmp",
            device=rule.device,
            last_seen_ts=last_ts,  # None → forces DOWN
            stale_seconds=stale_seconds,
            now=now,
        )

    # -------------------------------------------------
    # SERVER (rule-driven)
    # -------------------------------------------------
    server_seen = get_server_last_seen()

    server_rules = DeviceUpDownRule.query.filter_by(
        source="server",
        is_enabled=True
    ).all()

    logger.info(
        "Evaluating %d SERVER device rules",
        len(server_rules),
    )

    for rule in server_rules:
        last_ts = server_seen.get(rule.device)
        process_device(
            source="server",
            device=rule.device,
            last_seen_ts=last_ts,  # None → forces DOWN
            stale_seconds=stale_seconds,
            now=now,
        )

    # -------------------------------------------------
    # iDRAC (rule-driven)
    # -------------------------------------------------
    idrac_seen = _get_idrac_last_seen_for_customer(None)

    idrac_rules = DeviceUpDownRule.query.filter_by(
        source="idrac",
        is_enabled=True
    ).all()

    logger.info(
        "Evaluating %d iDRAC device rules",
        len(idrac_rules),
    )

    for rule in idrac_rules:
        last_ts = idrac_seen.get(rule.device)
        process_device(
            source="idrac",
            device=rule.device,
            last_seen_ts=last_ts,
            stale_seconds=stale_seconds,
            now=now,
        )

    # -------------------------------------------------
    # iLO (rule-driven)
    # -------------------------------------------------
    ilo_seen = _get_ilo_last_seen_for_customer(None)

    ilo_rules = DeviceUpDownRule.query.filter_by(
        source="ilo",
        is_enabled=True
    ).all()

    logger.info(
        "Evaluating %d iLO device rules",
        len(ilo_rules),
    )

    for rule in ilo_rules:
        last_ts = ilo_seen.get(rule.device)
        process_device(
            source="ilo",
            device=rule.device,
            last_seen_ts=last_ts,
            stale_seconds=stale_seconds,
            now=now,
        )

    db.session.commit()
    logger.info("DeviceUpDown cycle completed")

