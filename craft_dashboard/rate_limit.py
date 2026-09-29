"""Shared rate-limiting infrastructure.

Lives outside ``routes/`` so that services and other route modules can apply
limits without importing a sibling route module, which would invert the
``routes -> services -> repositories`` layering.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.util import get_remote_address

if TYPE_CHECKING:
    from collections.abc import Callable

#: Application-wide limiter. Registered on the app in ``app.py``; route
#: modules import this rather than constructing their own, so limits share a
#: single backing store.
limiter = Limiter(key_func=get_remote_address)


def is_local_caller(key: str) -> bool:
    """Return whether *key* (the caller's IP) counts as "local" for rate limits.

    The continuous ``evaluate`` worker runs in its own container on the shared
    ``vps-net`` Podman network (see docker-compose.llm-evaluate.yml) and calls
    craft-dashboard by its container hostname, never over loopback — so its
    source IP is a private container address (e.g. ``10.89.0.x``), not
    ``127.0.0.1``. A literal-loopback check would misclassify it as an external
    caller, throttling it under ``--concurrency > 1``, which can back the worker
    off long enough for a ``/next`` lock to expire and the same issue to be
    evaluated twice. Any private (RFC 1918/RFC 4193/loopback) address is treated
    as local instead, since only same-host/same-network containers can present
    one here.
    """
    try:
        return ipaddress.ip_address(key).is_private
    except ValueError:
        return False


def local_aware_limit(local: str, remote: str) -> Callable[[str], str]:
    """Build a ``slowapi`` limit callable that varies by caller locality.

    ``slowapi`` calls the returned function with the resolved rate-limit key
    (the result of ``key_func``, i.e. the caller's IP) when the decorated limit
    value is a callable declaring a ``key`` parameter, letting the limit vary
    per caller without inspecting the request directly.
    """

    def _limit(key: str) -> str:
        return local if is_local_caller(key) else remote

    return _limit
