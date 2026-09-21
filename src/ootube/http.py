"""HTTP helpers.

Trend sources are third-party and flaky, so every fetch retries with backoff
and callers treat failure as "no signals from this source" rather than an
exception. A single 503 from Reddit must not abort a scheduled run.

Retries are selective. Retrying a transient 503 is useful; retrying a proxy
policy denial or a DNS failure just multiplies a guaranteed failure by the
retry count. With ~30 feeds configured, blindly retrying a network-level block
turns a fast failure into minutes of dead waiting on every run.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, TypeVar

import requests
from requests.exceptions import ProxyError, SSLError, TooManyRedirects

log = logging.getLogger(__name__)

USER_AGENT = "ootube/0.1 (+https://github.com/lcross685-source/Ootube-bot)"
DEFAULT_TIMEOUT = 20

T = TypeVar("T")
R = TypeVar("R")

#: Statuses worth a second attempt. 4xx (other than 429) means the request
#: itself is wrong, so repeating it verbatim cannot help.
RETRIABLE_STATUS = {429, 500, 502, 503, 504}

#: Failures that will not change on retry: an egress policy denial, a TLS
#: trust problem, or a redirect loop.
PERMANENT_EXCEPTIONS = (ProxyError, SSLError, TooManyRedirects)


class PermanentHTTPError(Exception):
    """A failure that retrying cannot fix."""


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 3,
    backoff: float = 1.5,
) -> requests.Response | None:
    """GET with selective retries. Returns ``None`` instead of raising."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    hdrs.update(headers or {})
    delay = backoff

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code in RETRIABLE_STATUS:
                raise requests.HTTPError(f"status {resp.status_code}")
            if resp.status_code >= 400:
                # Permanent: wrong URL, auth failure, or a policy denial.
                log.warning("GET %s -> %s (not retrying)", url, resp.status_code)
                return None
            return resp
        except PERMANENT_EXCEPTIONS as exc:
            log.warning("GET %s blocked at the network layer (not retrying): %s", url, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            if attempt == retries:
                log.warning("GET %s failed after %d attempts: %s", url, retries, exc)
                return None
            time.sleep(delay)
            delay *= backoff
    return None


def get_json(url: str, **kwargs: Any) -> Any | None:
    resp = get(url, **kwargs)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("Non-JSON response from %s", url)
        return None


def fetch_many(
    items: Iterable[T],
    fn: Callable[[T], R],
    *,
    workers: int = 8,
) -> list[R]:
    """Run ``fn`` over ``items`` concurrently, preserving input order.

    Trend discovery is almost entirely network wait: a niche list of five
    verticals expands to roughly thirty feeds and subreddits, which takes
    minutes in sequence and seconds in parallel. Exceptions are swallowed per
    item so one bad feed cannot fail the batch.
    """
    items = list(items)
    if not items:
        return []
    if len(items) == 1 or workers <= 1:
        results = []
        for item in items:
            try:
                results.append(fn(item))
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch failed for %r: %s", item, exc)
        return results

    def _safe(item: T) -> R | None:
        try:
            return fn(item)
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch failed for %r: %s", item, exc)
            return None

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        out = list(pool.map(_safe, items))
    return [r for r in out if r is not None]
