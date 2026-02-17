# reports/fortigate/rpt_1009.py
import os
import time
import json
import tempfile
from datetime import datetime
from flask import current_app
from .pdf_1009 import build_pdf
from .excel_1009 import build_excel

INFLUX_QUERY_TEMPLATE = """
SELECT *
FROM sdwan_health
WHERE time >= '{start}' AND time <= '{end}'
AND hostname = '{device}' group by hc_name order by time desc limit 1
"""


def normalize_ts(ts):
    # Input: '2025-12-04T07:00' from datetime-local
    # Output: '2025-12-04T07:00:00Z'

    if ts is None:
        return ts

    ts = ts.strip()

    if len(ts) == 16:
        # "YYYY-MM-DDTHH:MM" → add seconds
        ts = ts + ":00"

    # If no timezone, append Z
    if "Z" not in ts and "+" not in ts:
        ts = ts + "Z"

    return ts


def _safe_float(v, default=0.0):
    if v is None:
        return default
    try:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if s.endswith('%'):
            s = s[:-1]
        return float(s)
    except Exception:
        return default

def _normalize_row(raw):
    # raw is dict-like row returned from influx (column -> value)
    r = {}
    # copy link name
    r['link_name'] = raw.get('fgVWLHealthCheckLinkName') or raw.get('hc_name') or raw.get('fgVWLHealthCheckLinkName') or raw.get('fgVWLHealthCheckLinkIfName') or raw.get('fgVWLHealthCheckLinkName') or raw.get('name') or ''
    # parse state: treat state == 1 as DOWN, else UP
    try:
        state = int(raw.get('fgVWLHealthCheckLinkState', 0))
    except Exception:
        # sometimes it's string "0" or "1", handle gracefully
        try:
            state = int(str(raw.get('fgVWLHealthCheckLinkState', '0')).strip())
        except Exception:
            state = 0
    r['state_raw'] = state
    r['status'] = 'DOWN' if state == 1 else 'UP'
    # latency/jitter: may be strings
    r['latency_ms'] = _safe_float(raw.get('fgVWLHealthCheckLinkLatency'), 0.0)
    r['jitter_ms'] = _safe_float(raw.get('fgVWLHealthCheckLinkJitter'), 0.0)
    # packet loss percent
    r['packet_loss'] = _safe_float(raw.get('fgVWLHealthCheckLinkPacketLoss'), 0.0)
    # MOS
    try:
        r['mos'] = float(raw.get('fgVWLHealthCheckLinkMOS') or 0.0)
    except Exception:
        r['mos'] = 0.0
    # bandwidth fields (in bytes or bits depending on ingestion) — preserve raw ints
    r['bandwidth_in'] = int(raw.get('bandwidth_in') or raw.get('fgVWLHealthCheckLinkBandwidthIn') or 0)
    r['bandwidth_out'] = int(raw.get('bandwidth_out') or raw.get('fgVWLHealthCheckLinkBandwidthOut') or 0)
    r['used_bandwidth_in'] = int(raw.get('fgVWLHealthCheckLinkUsedBandwidthIn') or 0)
    r['used_bandwidth_out'] = int(raw.get('fgVWLHealthCheckLinkUsedBandwidthOut') or 0)
    # interface / ifname
    r['ifname'] = raw.get('fgVWLHealthCheckLinkIfName') or raw.get('fgVWLHealthCheckLinkName') or raw.get('fgVWLHealthCheckLinkIfName') or ''
    # hostname / device
    r['hostname'] = raw.get('hostname') or raw.get('host') or ''
    # time (use the raw time if present)
    r['time'] = raw.get('time') or raw.get('time_stamp') or ''
    return r

def _flatten_influx_results(resp_json):
    """
    Convert influx query JSON to list of row dicts.
    Handles structure:
    {
      "results":[
        {"series":[ {"columns":["time","field1",...], "values":[ [...], ... ] } ]}
      ]
    }
    """
    rows = []
    try:
        res = resp_json.get('results', [])
        if not res:
            return rows
        series = res[0].get('series', [])
        for s in series:
            cols = s.get('columns', [])
            vals = s.get('values', [])
            for v in vals:
                # map columns -> value
                row = {}
                for i, c in enumerate(cols):
                    row[c] = v[i]
                # Influx sometimes places tags separately
                tags = s.get('tags', {})
                if tags:
                    for k, tv in tags.items():
                        row[k] = tv
                rows.append(row)
    except Exception:
        pass
    return rows

def run(device: str, start: str, end: str, fmt: str = "pdf"):
    """
    device: the Fortigate hostname/device name (device_name)
    start/end: ISO format or 'YYYY-MM-DDTHH:MM:SS' expected (we'll pass directly into Influx)
    fmt: "pdf" or "excel"
    """
    if not device:
        raise ValueError("device required")

    # Build query and call InfluxDB HTTP API
    influx_url = (
        current_app.config.get("FORTIGATE_INFLUXDB_URL")
        or current_app.config.get("INFLUXDB_URL")
        or os.environ.get("FORTIGATE_INFLUXDB_URL")
        or os.environ.get("INFLUXDB_URL")
        or "http://127.0.0.1:8086/query"
    )
    influx_db = (
        current_app.config.get("FORTIGATE_INFLUXDB_DB")
        or os.environ.get("FORTIGATE_INFLUXDB_DB")
        or "fortigate"
    )

    if not influx_url or not influx_db:
        raise ValueError("InfluxDB config missing")

    # Influx expects time in RFC3339; ensure format is acceptable.
    # If user provided datetime-local input, it might be like "2025-12-04T07:00"
    # We'll pass as-is; Influx accepts many formats. Optionally, append "Z" if timezone missing.
    start = normalize_ts(start)
    end   = normalize_ts(end)

    q = INFLUX_QUERY_TEMPLATE.format(start=start, end=end, device=device)

    print(q)

    params = {"db": influx_db, "q": q}
    import requests
    resp = requests.get(influx_url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    raw_rows = _flatten_influx_results(data)
    normalized = [_normalize_row(r) for r in raw_rows]

    # deduplicate by link name — keep latest by time if time present
    by_name = {}
    for r in normalized:
        key = r.get('link_name') or (r.get('ifname') or '') + '::' + (r.get('hostname') or '')
        # keep latest; if time parseable, compare
        prev = by_name.get(key)
        if prev is None:
            by_name[key] = r
        else:
            # choose non-zero latency preferred, or latest time if available
            by_name[key] = r  # simple override; can be improved if time comparators exist

    rows = list(by_name.values())

    # split into up / down
    up_links = [r for r in rows if r['status'] == 'UP']
    down_links = [r for r in rows if r['status'] == 'DOWN']

    # sort UP lists for top-N
    top_latency = sorted([r for r in up_links if r['latency_ms'] > 0], key=lambda x: x['latency_ms'], reverse=True)[:10]
    top_jitter = sorted([r for r in up_links if r['jitter_ms'] > 0], key=lambda x: x['jitter_ms'], reverse=True)[:10]
    top_packet_loss = sorted([r for r in up_links], key=lambda x: x['packet_loss'], reverse=True)[:10]

    # basic metadata
    meta = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "device": device,
        "start": start,
        "end": end,
        "total_links": len(rows),
        "up_links": len(up_links),
        "down_links": len(down_links)
    }

    # Prepare output file path
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    outdir = current_app.config.get("REPORTS_OUTDIR", "/tmp")
    os.makedirs(outdir, exist_ok=True)
    if fmt == "pdf":
        outfile = os.path.join(outdir, f"rpt_1009_sdwan_{device}_{ts}.pdf")
        build_pdf(outfile, meta, top_latency, top_jitter, top_packet_loss, up_links, down_links, rows)
    else:
        outfile = os.path.join(outdir, f"rpt_1009_sdwan_{device}_{ts}.xlsx")
        build_excel(outfile, meta, top_latency, top_jitter, top_packet_loss, up_links, down_links, rows)

    return outfile


class FortigateSdwanReport:
    """
    Wrapper class so report_routes.py can call rpt.run().
    """
    @staticmethod
    def run(device: str, start: str, end: str, fmt: str = "pdf"):
        return run(device, start, end, fmt)

