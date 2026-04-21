from __future__ import annotations

from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_hardened_session(
    *,
    user_agent: str,
    pool_connections: int = 16,
    pool_maxsize: int = 32,
    total_retries: int = 2,
    backoff_factor: float = 0.2,
    status_forcelist: Iterable[int] = (429, 500, 502, 503, 504),
) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": str(user_agent)})
    retries = Retry(
        total=max(0, int(total_retries)),
        connect=max(0, int(total_retries)),
        read=max(0, int(total_retries)),
        status=max(0, int(total_retries)),
        allowed_methods=frozenset({"GET", "POST"}),
        status_forcelist=frozenset(int(x) for x in status_forcelist),
        backoff_factor=max(0.0, float(backoff_factor)),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=max(1, int(pool_connections)),
        pool_maxsize=max(1, int(pool_maxsize)),
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
