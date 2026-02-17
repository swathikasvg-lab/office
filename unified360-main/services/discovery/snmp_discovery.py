import ipaddress
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from pysnmp.hlapi import *
from extensions import db
from models.discovery import DiscoveredAsset

# ------------ SNMP GET HELPER ------------
def snmp_get(ip, creds, oid):
    try:
        if creds.snmp_version == "v2c":
            auth = CommunityData(creds.community, mpModel=1)
        else:
            auth = UsmUserData(
                creds.username,
                creds.auth_password if creds.auth_protocol else None,
                creds.priv_password if creds.priv_protocol else None,
                authProtocol=usmHMACSHAAuthProtocol if creds.auth_protocol == "SHA" else usmHMACMD5AuthProtocol,
                privProtocol=usmAesCfb128Protocol if creds.priv_protocol == "AES" else usmDESPrivProtocol
            )

        iterator = getCmd(
            SnmpEngine(),
            auth,
            UdpTransportTarget((ip, 161), timeout=1, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )

        errInd, errStat, errIdx, varBinds = next(iterator)

        if errInd or errStat:
            return None

        return str(varBinds[0][1])

    except:
        return None

# ------------ DEVICE IDENTIFICATION ------------
def fingerprint_device(sysObjectID, sysDescr):
    s = (sysDescr or "").lower()

    if "cisco" in s or sysObjectID.startswith("1.3.6.1.4.1.9"):
        return "Cisco"
    if "fortigate" in s or "fortinet" in s or sysObjectID.startswith("1.3.6.1.4.1.12356"):
        return "Fortinet"
    if "aruba" in s or sysObjectID.startswith("1.3.6.1.4.1.14823"):
        return "Aruba/HPE"
    if "hewlett" in s:
        return "HP"
    if "dell" in s:
        return "Dell"
    if "router" in s:
        return "Router"
    if "printer" in s:
        return "Printer"
    if "camera" in s or "hikvision" in s:
        return "CCTV"
    return "Unknown"


def classify_device(vendor, sysDescr):
    s = sysDescr.lower()

    if vendor == "Fortinet":
        return "firewall"
    if vendor == "Cisco":
        if "ios" in s or "cat" in s or "switch" in s:
            return "switch"
        if "router" in s:
            return "router"
        if "controller" in s:
            return "wireless_controller"

    if vendor == "Aruba/HPE":
        if "controller" in s:
            return "wireless_controller"
        if "ap-" in s or "access point" in s:
            return "ap"

    if "printer" in s:
        return "printer"
    if "camera" in s:
        return "cctv"

    return "unknown"


# ------------ DISCOVER ONE IP ------------
def discover_one(ip, creds):
    sysObjectID = snmp_get(ip, creds, "1.3.6.1.2.1.1.2.0")
    if not sysObjectID:
        return None

    sysDescr = snmp_get(ip, creds, "1.3.6.1.2.1.1.1.0") or ""
    sysName = snmp_get(ip, creds, "1.3.6.1.2.1.1.5.0") or ""

    vendor = fingerprint_device(sysObjectID, sysDescr)
    device_type = classify_device(vendor, sysDescr)

    now = datetime.utcnow()

    return {
        "ip": ip,
        "hostname": sysName,
        "vendor": vendor,
        "model": sysDescr,
        "device_type": device_type,
        "sys_object_id": sysObjectID,
        "sys_descr": sysDescr,
        "snmp_profile": "default",
        "snmp_version": "v2c",
        "last_seen": now,
        "first_seen": now,
    }


# ------------ DISCOVER RANGE ------------
def discover_range(cidr, credential_obj):
    network = ipaddress.ip_network(cidr, strict=False)
    ips = [str(i) for i in network.hosts()]

    found = []

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {pool.submit(discover_one, ip, credential_obj): ip for ip in ips}
        for f in futures:
            res = f.result()
            if res:
                found.append(res)

    # Save to DB
    for d in found:
        existing = DiscoveredAsset.query.filter_by(ip_address=d["ip"]).first()
        if existing:
            existing.hostname = d["hostname"]
            existing.vendor = d["vendor"]
            existing.model = d["model"]
            existing.device_type = d["device_type"]
            existing.sys_object_id = d["sys_object_id"]
            existing.sys_descr = d["sys_descr"]
            existing.last_seen = datetime.utcnow()
        else:
            obj = DiscoveredAsset(
                ip_address=d["ip"],
                hostname=d["hostname"],
                vendor=d["vendor"],
                model=d["model"],
                device_type=d["device_type"],
                sys_object_id=d["sys_object_id"],
                sys_descr=d["sys_descr"],
                snmp_profile=d["snmp_profile"],
                snmp_version=d["snmp_version"],
            )
            db.session.add(obj)

    db.session.commit()
    return found

