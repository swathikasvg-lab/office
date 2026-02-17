from types import SimpleNamespace

from routes import itom_routes


def _svc(sid, cid=1):
    return SimpleNamespace(id=sid, customer_id=cid)


def _binding(service_id, mtype, ref):
    return SimpleNamespace(
        id=1000 + service_id,
        service_id=service_id,
        monitor_type=mtype,
        monitor_ref=ref,
        display_name=None,
    )


def _dep(did, parent, child, dep_type="hard"):
    return SimpleNamespace(
        id=did,
        parent_service_id=parent,
        child_service_id=child,
        dependency_type=dep_type,
    )


def test_compute_service_health_direct_down_and_affected_chain():
    s1 = _svc(1)
    s2 = _svc(2)
    bindings = [_binding(2, "ping", "host-b")]
    deps = [_dep(1, 1, 2, "hard")]
    active_keys = {"host-b", "ping:host-b"}

    status, reasons, affected = itom_routes._compute_service_health(
        [s1, s2], bindings, deps, active_keys=active_keys
    )

    assert status[2] == "DOWN"
    assert status[1] == "IMPACTED"
    assert 1 in affected[2]
    assert any(r.get("reason") == "active_alert" for r in reasons[2])


def test_compute_service_health_soft_dependency_degraded():
    s1 = _svc(1)
    s2 = _svc(2)
    bindings = [_binding(2, "url", "https://app.local")]
    deps = [_dep(1, 1, 2, "soft")]
    active_keys = {"https://app.local", "url:https://app.local"}

    status, _, _ = itom_routes._compute_service_health(
        [s1, s2], bindings, deps, active_keys=active_keys
    )

    assert status[2] == "DOWN"
    assert status[1] == "DEGRADED"
