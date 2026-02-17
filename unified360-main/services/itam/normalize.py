import ipaddress
import re


_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def norm_str(value):
    if value is None:
        return ""
    return str(value).strip()


def norm_lower(value):
    return norm_str(value).lower()


def norm_hostname(value):
    host = norm_lower(value)
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def norm_mac(value):
    s = norm_lower(value)
    if not s:
        return ""
    s = re.sub(r"[^0-9a-f]", "", s)
    if len(s) != 12:
        return ""
    return ":".join([s[i : i + 2] for i in range(0, 12, 2)])


def norm_ip(value):
    s = norm_str(value)
    if not s:
        return ""

    if ":" in s and s.count(".") >= 1:
        # host:port shape
        s = s.split(":", 1)[0]

    try:
        return str(ipaddress.ip_address(s))
    except Exception:
        return ""


def maybe_ip_from_text(value):
    s = norm_str(value)
    if not s:
        return ""
    if ":" in s and s.count(".") >= 1:
        s = s.split(":", 1)[0]
    if _IPV4_RE.match(s):
        return norm_ip(s)
    return ""


def classify_asset(data):
    source = norm_lower(data.get("source_name"))
    hint = norm_lower(data.get("asset_type_hint"))
    template = norm_lower(data.get("template"))
    os_name = norm_lower(data.get("os_name") or data.get("os"))

    if "cloud" in source or data.get("cloud_instance_id"):
        return "cloud_asset"
    if "desktop" in source or hint == "workstation":
        return "workstation"
    if "ot" in source or hint in {"ot_asset", "ot_device"}:
        return "ot_device"
    if "snmp" in source or template:
        if "fortigate" in template or "switch" in template or "firewall" in template:
            return "network_device"
    if hint:
        return hint
    if "server" in source:
        return "server"
    if any(x in os_name for x in ["windows", "linux", "ubuntu", "red hat", "centos", "debian"]):
        return "server"
    return "unknown"
