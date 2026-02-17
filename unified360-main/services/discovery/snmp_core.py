# services/discovery/snmp_core.py
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class SNMPCreds:
    """
    Generic SNMP credentials object.

    You can pass:
    - an instance of this class, or
    - any object with the same attributes (e.g. models.SnmpConfig)
    """
    snmp_version: str = "v2c"
    community: Optional[str] = None

    # v3 fields (optional)
    v3_username: Optional[str] = None
    v3_auth_protocol: Optional[str] = None  # MD5 / SHA
    v3_auth_password: Optional[str] = None
    v3_priv_protocol: Optional[str] = None  # AES / DES
    v3_priv_password: Optional[str] = None


def _build_v2c_cmd(ip: str, community: str, oid: str,
                   timeout: int = 2, retries: int = 1) -> list[str]:
    """
    Build an snmpget command for SNMP v2c.
    """
    return [
        "snmpget",
        "-v2c",
        "-c", community,
        "-t", str(timeout),
        "-r", str(retries),
        "-Oqv",          # value only
        ip,
        oid,
    ]


def _build_v3_cmd(ip: str, creds: SNMPCreds, oid: str,
                  timeout: int = 2, retries: int = 1) -> list[str]:
    """
    Build an snmpget command for SNMP v3.
    We infer the security level (noAuthNoPriv / authNoPriv / authPriv)
    from which passwords are set.
    """
    username = creds.v3_username or ""
    auth_proto = (creds.v3_auth_protocol or "MD5").upper()
    priv_proto = (creds.v3_priv_protocol or "AES").upper()
    auth_pwd = creds.v3_auth_password or ""
    priv_pwd = creds.v3_priv_password or ""

    # Decide security level
    if auth_pwd and priv_pwd:
        sec_level = "authPriv"
    elif auth_pwd and not priv_pwd:
        sec_level = "authNoPriv"
    else:
        sec_level = "noAuthNoPriv"

    cmd = [
        "snmpget",
        "-v3",
        "-u", username,
        "-l", sec_level,
        "-t", str(timeout),
        "-r", str(retries),
        "-Oqv",
    ]

    if sec_level in ("authNoPriv", "authPriv"):
        cmd += ["-a", auth_proto, "-A", auth_pwd]

    if sec_level == "authPriv":
        cmd += ["-x", priv_proto, "-X", priv_pwd]

    cmd += [ip, oid]
    return cmd


def snmp_get(ip: str, creds, oid: str,
             timeout: int = 2, retries: int = 1) -> Optional[str]:
    """
    Perform a single SNMP GET using system `snmpget`.

    Returns:
        - string value (already stripped) on success
        - None on any failure
    """
    # Normalize into SNMPCreds
    if isinstance(creds, SNMPCreds):
        c = creds
    else:
        # works with your models.SnmpConfig as well
        c = SNMPCreds(
            snmp_version=getattr(creds, "snmp_version", "v2c"),
            community=getattr(creds, "community", None),
            v3_username=getattr(creds, "v3_username", None),
            v3_auth_protocol=getattr(creds, "v3_auth_protocol", None),
            v3_auth_password=getattr(creds, "v3_auth_password", None),
            v3_priv_protocol=getattr(creds, "v3_priv_protocol", None),
            v3_priv_password=getattr(creds, "v3_priv_password", None),
        )

    version = (c.snmp_version or "v2c").lower()

    if version == "v2c":
        if not c.community:
            return None
        cmd = _build_v2c_cmd(ip, c.community, oid, timeout=timeout, retries=retries)
    elif version == "v3":
        if not c.v3_username:
            return None
        cmd = _build_v3_cmd(ip, c, oid, timeout=timeout, retries=retries)
    else:
        return None

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 1,
        )
    except Exception:
        return None

    if proc.returncode != 0:
        return None

    value = (proc.stdout or "").strip()
    if value == "" or value.lower().startswith("timeout"):
        return None
    return value

