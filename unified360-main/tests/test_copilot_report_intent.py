from routes import copilot_routes


def test_detect_report_by_id():
    r = copilot_routes._detect_report("run report 1008 for device:FGT-01")
    assert r is not None
    assert r["id"] == 1008


def test_parse_report_format_defaults_pdf():
    assert copilot_routes._parse_report_format("weekly report") == "pdf"
    assert copilot_routes._parse_report_format("export in excel") == "excel"


def test_build_report_intent_required_fields():
    intent = copilot_routes._build_report_intent(
        "generate bandwidth report last 7 days in pdf template:Fortigate"
    )
    assert intent is not None
    assert intent["report_id"] == 1005
    # 1005 requires template_type, device_name, instance
    assert "device_name" in intent["missing_fields"]
    assert "instance" in intent["missing_fields"]
    assert intent["params"]["template_type"] == "Fortigate"
