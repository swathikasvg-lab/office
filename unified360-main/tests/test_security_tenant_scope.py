from types import SimpleNamespace

import security


def test_allowed_customer_global_admin_unrestricted():
    user = SimpleNamespace(is_admin=True, customer_id=None)
    assert security.get_allowed_customer_id(user) is None


def test_allowed_customer_admin_bound_to_customer():
    user = SimpleNamespace(is_admin=True, customer_id=55)
    assert security.get_allowed_customer_id(user) == 55


def test_allowed_customer_regular_user():
    user = SimpleNamespace(is_admin=False, customer_id=7)
    assert security.get_allowed_customer_id(user) == 7
