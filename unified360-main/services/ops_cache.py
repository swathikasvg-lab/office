from datetime import datetime, timedelta, timezone
from threading import Lock


_CACHE = {}
_LOCK = Lock()


def _now():
    return datetime.now(timezone.utc)


def get(key):
    with _LOCK:
        row = _CACHE.get(key)
        if not row:
            return None
        if row["expires_at"] <= _now():
            _CACHE.pop(key, None)
            return None
        return row["value"]


def set_value(key, value, ttl_seconds=30):
    ttl = max(1, int(ttl_seconds or 30))
    with _LOCK:
        _CACHE[key] = {
            "value": value,
            "expires_at": _now() + timedelta(seconds=ttl),
        }


def cached(key, ttl_seconds, builder):
    hit = get(key)
    if hit is not None:
        return hit
    value = builder()
    set_value(key, value, ttl_seconds=ttl_seconds)
    return value


def invalidate(prefix=None):
    with _LOCK:
        if not prefix:
            _CACHE.clear()
            return
        for k in list(_CACHE.keys()):
            if str(k).startswith(prefix):
                _CACHE.pop(k, None)
