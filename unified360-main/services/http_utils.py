import time
from typing import Any, Optional

import requests


def get_json_with_retry(
    url: str,
    params: Optional[Any] = None,
    timeout: int = 15,
    retries: int = 2,
    backoff_seconds: float = 0.4,
    session: Optional[requests.Session] = None,
):
    """
    GET JSON with small retry/backoff.
    Raises the last exception if all attempts fail.
    """
    last_exc = None
    caller = session or requests

    for i in range(retries + 1):
        try:
            resp = caller.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_exc = e
            if i < retries:
                time.sleep(backoff_seconds * (i + 1))
    raise last_exc
