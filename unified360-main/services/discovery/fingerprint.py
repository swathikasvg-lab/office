# services/discovery/fingerprint.py

def fingerprint(sysObjectID, sysDescr):
    sysObjectID = sysObjectID or ""
    sysDescr = (sysDescr or "").lower()

    # --- Vendor match ---
    if "cisco" in sysDescr or ".9.9." in sysObjectID:
        vendor = "Cisco"
    elif "fortigate" in sysDescr or "fortios" in sysDescr or "12356" in sysObjectID:
        vendor = "Fortinet"
    elif "arista" in sysDescr:
        vendor = "Arista"
    elif "palo" in sysDescr:
        vendor = "Palo Alto"
    else:
        vendor = "Unknown"

    # --- Device type ---
    if "fw" in sysDescr or "firewall" in sysDescr:
        dtype = "Firewall"
    elif "switch" in sysDescr or "ethernet" in sysDescr:
        dtype = "Switch"
    elif "router" in sysDescr:
        dtype = "Router"
    elif "controller" in sysDescr:
        dtype = "Wireless Controller"
    elif "access point" in sysDescr or "ap" in sysDescr:
        dtype = "Wireless AP"
    else:
        dtype = "Unknown"

    return vendor, dtype

