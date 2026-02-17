# services/discovery/discover.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from ipaddress import ip_address, ip_network
from typing import Iterable, List, Dict, Optional

from .snmp_core import snmp_get, SNMPCreds

# Standard SNMP system OIDs
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"


def _parse_ip_range(ip_range: str) -> List[str]:
    """
    Accepts:
      - single IP: '10.10.10.5'
      - CIDR: '10.10.10.0/24'
      - range: '10.10.10.10-10.10.10.50'

    Returns:
      list of string IPs.
    """
    ip_range = (ip_range or "").strip()
    if not ip_range:
        return []

    # address range form: a-b
    if "-" in ip_range:
        start_str, end_str = [x.strip() for x in ip_range.split("-", 1)]
        start = ip_address(start_str)
        end = ip_address(end_str)
        if int(end) < int(start):
            start, end = end, start
        result = []
        cur = int(start)
        last = int(end)
        while cur <= last:
            result.append(str(ip_address(cur)))
            cur += 1
        return result

    # CIDR form
    if "/" in ip_range:
        net = ip_network(ip_range, strict=False)
        return [str(ip) for ip in net.hosts()]

    # single IP
    _ = ip_address(ip_range)  # will raise if invalid
    return [ip_range]


def _classify(sys_object_id: str, sys_descr: str) -> Dict[str, str]:
    """
    Very rough vendor/model/type classification.
    You can extend this mapping as you like.
    """
    vendor = "Unknown"
    model = ""
    device_type = "Network Device"

    soid = sys_object_id or ""
    desc = (sys_descr or "").lower()

    # Vendor by enterprise OID
    if soid.startswith("1.3.6.1.4.1.9."):
        vendor = "Cisco"
    elif soid.startswith("1.3.6.1.4.1.12356."):
        vendor = "Fortinet"
    elif soid.startswith("1.3.6.1.4.1.2636."):
        vendor = "Juniper"
    elif soid.startswith("1.3.6.1.4.1.11."):
        vendor = "HPE"
    elif soid.startswith("1.3.6.1.4.1.22736."):
        vendor = "Aruba"
    elif soid.startswith("1.3.6.1.4.1.8072."):
        vendor = "Net-SNMP"

    # Device type from description
    if "switch" in desc:
        device_type = "Switch"
    elif "router" in desc:
        device_type = "Router"
    elif "firewall" in desc or "fortigate" in desc or "asa" in desc:
        device_type = "Firewall"
    elif "controller" in desc:
        device_type = "Wireless Controller"
    elif "access point" in desc or "ap " in desc:
        device_type = "Access Point"
    elif "printer" in desc:
        device_type = "Printer"
    elif "camera" in desc or "cctv" in desc:
        device_type = "Camera"

    # crude model extraction
    model = ""
    if vendor != "Unknown":
        # often first token(s) show model, e.g. "Cisco IOS Software [C9300-24T] ..."
        parts = sys_descr.split(",")[0].strip()
        model = parts

    return {
        "vendor": vendor,
        "model": model,
        "device_type": device_type,
    }


def _probe_ip(ip: str, creds) -> Optional[Dict]:
    """
    Probe a single IP with SNMP.

    Returns a dict with discovered fields, or None if SNMP is not responding.
    """
    # sysObjectID is best initial proof of a proper SNMP agent
    soid = snmp_get(ip, creds, SYS_OBJECT_ID)
    if not soid:
        return None

    sdesc = snmp_get(ip, creds, SYS_DESCR) or ""
    sname = snmp_get(ip, creds, SYS_NAME) or ""

    meta = _classify(soid, sdesc)

    # Allow passing SnmpConfig or SNMPCreds; try to reuse profile if present
    snmp_version = getattr(creds, "snmp_version", "v2c")
    snmp_profile = getattr(creds, "name", None) or getattr(creds, "profile", None) or "default"

    return {
        "ip_address": ip,
        "hostname": sname or None,
        "vendor": meta["vendor"],
        "model": meta["model"],
        "device_type": meta["device_type"],
        "sys_object_id": soid,
        "sys_descr": sdesc,
        "snmp_profile": snmp_profile,
        "snmp_version": snmp_version,
    }


def run_discovery(ip_range: str,
                  creds,
                  max_workers: int = 32) -> List[Dict]:
    """
    Run SNMP discovery over a given IP range using shared credentials.

    Args:
        ip_range: "10.10.10.5", "10.10.10.0/24" or "10.10.10.10-10.10.10.50"
        creds: an object with SNMP fields (SnmpConfig or SNMPCreds)
        max_workers: threads for parallel SNMP GETs

    Returns:
        List of dicts with keys matching DiscoveredAsset fields:
        - ip_address
        - hostname
        - vendor
        - model
        - device_type
        - sys_object_id
        - sys_descr
        - snmp_profile
        - snmp_version
    """
    ips = _parse_ip_range(ip_range)
    if not ips:
        return []

    results: List[Dict] = []
    workers = min(max_workers, len(ips)) or 1

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_probe_ip, ip, creds): ip for ip in ips}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception:
                continue
            if res:
                results.append(res)

    # sort by IP for stable display
    results.sort(key=lambda x: x["ip_address"])
    return results

