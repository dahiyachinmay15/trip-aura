"""HTTP client for open-data services (Nominatim, Overpass, Wikimedia).

Follows the services' usage policies: identifying User-Agent, at most one
Nominatim request per second, and failover across Overpass mirrors. Errors are
raised as ProviderRequestError; nothing is replaced with mock data.

Network behaviour of GET requests:
- one pooled client keeps connections alive, so repeat calls to a host skip the TCP and TLS handshakes;
- a short failure (connection refused or reset, 502/503/504, 429 with a short Retry-After) is retried once after an
  exponential backoff with jitter; a timeout is not retried, since it already used up its time;
- a circuit breaker per host: after several failures in a row the host is skipped for a minute, so a service that is
  down costs one fast error per plan instead of a timeout per request; then one trial request is let through.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.core.errors import ProviderRequestError

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 502, 503, 504})
RETRY_ERRORS = (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)
MAX_RETRY_AFTER_S = 3.0      # a server asking for a longer pause is not retried within the request


class CircuitBreaker:
    """Per-host failure counter. Closed: requests pass. Open (after `threshold` failures in a row): requests are refused
    for `cooldown` seconds. Then half-open: one request passes; success closes it, failure opens it again."""

    def __init__(self, threshold: int = 4, cooldown: float = 60.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self._state: dict[str, tuple[int, float]] = {}    # host -> (failures in a row, open until)
        self._lock = threading.Lock()

    def allow(self, host: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            failures, until = self._state.get(host, (0, 0.0))
            if failures < self.threshold:
                return True
            if now < until:
                return False
            # half-open: let this one through, and hold the others until it has answered
            self._state[host] = (failures, now + self.cooldown)
            return True

    def success(self, host: str) -> None:
        with self._lock:
            self._state.pop(host, None)

    def failure(self, host: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            failures = self._state.get(host, (0, 0.0))[0] + 1
            self._state[host] = (failures, now + self.cooldown if failures >= self.threshold else 0.0)
            if failures == self.threshold:
                log.warning("%s failed %d times in a row; skipping it for %.0f s", host, failures, self.cooldown)


def backoff(attempt: int, base: float = 0.4, cap: float = 3.0) -> float:
    """Exponential backoff with jitter: about base * 2^attempt, randomised so many clients do not retry in step."""
    return min(cap, base * 2 ** attempt) * random.uniform(0.5, 1.0)


class OpenHttp:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._http = httpx.Client(
            timeout=settings.open_http_timeout_seconds, transport=transport,
            headers={"User-Agent": settings.open_user_agent, "Accept": "application/json",
                     "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
            # connection reuse (HTTP keep-alive): a city load makes many calls to the same few hosts
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16, keepalive_expiry=30),
        )
        self.breaker = CircuitBreaker()
        self._nominatim_lock = threading.Lock()
        self._last_nominatim = 0.0

    def get_json(self, url: str, params: dict[str, Any] | None = None, *, what: str,
                 timeout: float | None = None, retries: int = 1) -> Any:
        host = urlsplit(url).netloc
        if not self.breaker.allow(host):
            raise ProviderRequestError(f"{what}: skipped for now after repeated failures", details={"host": host})
        for attempt in range(retries + 1):
            last = attempt == retries
            try:
                r = self._http.get(url, params=params, **({"timeout": timeout} if timeout else {}))
            except RETRY_ERRORS as exc:
                if not last:
                    time.sleep(backoff(attempt))
                    continue
                self.breaker.failure(host)
                raise ProviderRequestError(f"{what}: could not connect ({exc.__class__.__name__})") from exc
            except httpx.HTTPError as exc:   # timeouts and the rest: no retry
                self.breaker.failure(host)
                raise ProviderRequestError(f"{what}: could not connect ({exc.__class__.__name__})") from exc
            if r.status_code in RETRY_STATUS and not last:
                pause = _retry_after(r)
                if pause is not None and pause <= MAX_RETRY_AFTER_S:
                    time.sleep(max(pause, backoff(attempt)))
                    continue
            if r.status_code >= 500 or r.status_code == 429:
                self.breaker.failure(host)
                raise ProviderRequestError(f"{what}: HTTP {r.status_code}", details={"url": url})
            if r.status_code >= 400:   # the request was wrong, the host is fine
                self.breaker.success(host)
                raise ProviderRequestError(f"{what}: HTTP {r.status_code}", details={"url": url})
            self.breaker.success(host)
            try:
                return r.json()
            except ValueError as exc:
                raise ProviderRequestError(f"{what}: invalid JSON response") from exc
        raise ProviderRequestError(f"{what}: no response")   # not reached: the last attempt returns or raises

    def nominatim(self, path: str, params: dict[str, Any]) -> Any:
        with self._nominatim_lock:  # usage policy: max 1 request/second
            wait = 1.1 - (time.monotonic() - self._last_nominatim)
            if wait > 0:
                time.sleep(wait)
            try:
                return self.get_json(f"{self.settings.nominatim_url.rstrip('/')}/{path}",
                                     {**params, "format": "jsonv2"}, what="Nominatim geocoding", timeout=15)
            finally:
                self._last_nominatim = time.monotonic()

    def overpass(self, query: str, deadline: float | None = None) -> dict:
        """Tries each mirror in turn; stops when `deadline` (time.monotonic()) has passed."""
        errors: list[str] = []
        for url in self.settings.overpass_url_list:
            t0 = time.monotonic()
            remaining = (deadline - t0) if deadline is not None else self.settings.open_http_timeout_seconds
            if remaining < 5:
                errors.append("time budget used up")
                break
            try:
                # httpx timeouts are per phase, so a slow connect must not be allowed the whole budget as well
                limit = min(65.0, remaining)
                r = self._http.post(url, data={"data": query}, timeout=httpx.Timeout(limit, connect=min(8.0, limit)))
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {exc.__class__.__name__}")
                log.warning("Overpass mirror %s failed: %s", url, exc.__class__.__name__)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    errors.append(f"{url}: invalid JSON")
                    continue
                remark = data.get("remark", "")
                if "runtime error" in remark or "timed out" in remark:
                    errors.append(f"{url}: {remark[:120]}")
                    continue
                log.info("Overpass %s answered in %.1fs (%d elements)", url, time.monotonic() - t0,
                         len(data.get("elements", [])))
                return data
            errors.append(f"{url}: HTTP {r.status_code}")
            log.warning("Overpass mirror %s returned %s", url, r.status_code)
        raise ProviderRequestError("OpenStreetMap (Overpass) is unavailable right now: " + "; ".join(errors))


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a Retry-After header in its delay form; 502/503/504 without one get the normal backoff (0)."""
    value = r.headers.get("retry-after")
    if value is None:
        return 0.0 if r.status_code != 429 else None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None   # the HTTP-date form: treat as "too long to wait here"
