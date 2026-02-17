from routes import itom_routes


def test_normalize_monitor_type_maps_variants():
    assert itom_routes._normalize_monitor_type("SNMP_Interface") == "snmp"
    assert itom_routes._normalize_monitor_type("service down") == "server"
    assert itom_routes._normalize_monitor_type("url") == "url"
    assert itom_routes._normalize_monitor_type("unknown_type") is None


def test_normalize_ref_for_type_server():
    assert itom_routes._normalize_ref_for_type("server", "host1|net|eth0") == "host1"
    assert itom_routes._normalize_ref_for_type("desktop", "desk01|disk|C:") == "desk01"
    assert itom_routes._normalize_ref_for_type("snmp", "10.1.1.1::ifDescr") == "10.1.1.1"
