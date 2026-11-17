"""HTTP and network behaviour: request ids, caching and ETags, compression, rate-limit headers, the body size limit,
and the outbound client's retries and circuit breaker."""
import gzip

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ProviderRequestError
from app.providers.open import http as open_http
from app.providers.open.http import CircuitBreaker, OpenHttp


# ------------------------------------------------------------------ request ids and timing
def test_every_response_has_a_request_id_and_timing(client):
    r = client.get("/api/health")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32 and r.headers["server-timing"].startswith("app;dur=")
    assert client.get("/api/health").headers["x-request-id"] != rid   # one per request

    # a safe id from a proxy is kept, so one request can be followed through Vercel, Azure and the logs
    assert client.get("/api/health", headers={"X-Request-ID": "vercel-abc123"}).headers["x-request-id"] == "vercel-abc123"
    # anything else is replaced (it would be written to the logs)
    bad = client.get("/api/health", headers={"X-Request-ID": "x\r\ny"}).headers["x-request-id"]
    assert bad != "x\r\ny" and len(bad) == 32


def test_an_unexpected_error_reports_its_request_id(client, base_request, new_client):
    from app.main import app
    from app.routers.deps import get_itinerary_service

    def broken():
        raise RuntimeError("boom")

    app.dependency_overrides[get_itinerary_service] = broken
    try:
        r = new_client(raise_server_exceptions=False).post("/api/trips/preview", json=base_request)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 500
    assert r.json()["error"]["details"]["request_id"] == r.headers["x-request-id"]


# ------------------------------------------------------------------ caching and conditional requests
def test_public_lookups_are_cacheable_and_revalidate_with_304(client):
    r = client.get("/api/meta/options")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=3600"
    etag = r.headers["etag"]
    assert etag.startswith('W/"')

    again = client.get("/api/meta/options", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == etag
    # a list of validators, with or without the weak prefix, still matches
    assert client.get("/api/meta/options", headers={"If-None-Match": f'"zzz", {etag[2:]}'}).status_code == 304
    assert client.get("/api/meta/options", headers={"If-None-Match": 'W/"other"'}).status_code == 200

    cities = client.get("/api/geo/cities", params={"q": "jaipur"})
    assert cities.headers["cache-control"] == "public, max-age=86400" and "etag" in cities.headers
    assert client.get("/api/meta/status").headers["cache-control"] == "no-cache"


def test_personal_answers_are_never_stored(client, base_request, monkeypatch):
    from app.routers import geo

    r = client.post("/api/trips/preview", json=base_request)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and "etag" not in r.headers
    resp = httpx.Response(200, json={"features": []}, request=httpx.Request("GET", "https://photon.test"))
    monkeypatch.setattr(geo, "_http_get", lambda *a, **k: resp)
    assert client.get("/api/geo/addresses", params={"q": "rossio"}).headers["cache-control"] == "no-store"
    assert client.get("/api/geo/reverse", params={"lat": 1, "lng": 2}).headers["cache-control"] == "no-store"
    # an error is never cached, even on a cacheable path
    assert client.get("/api/geo/cities", params={"q": "x"}).headers["cache-control"] == "no-store"


def test_new_security_headers(client):
    h = client.get("/api/health").headers
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert h["x-permitted-cross-domain-policies"] == "none"


# ------------------------------------------------------------------ compression
def test_large_answers_are_compressed(client, base_request):
    raw = client.post("/api/trips/preview", json=base_request, headers={"Accept-Encoding": "gzip"})
    assert raw.headers["content-encoding"] == "gzip" and "accept-encoding" in raw.headers["vary"].lower()
    plain = client.post("/api/trips/preview", json=base_request, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    # what went over the wire is much smaller than the JSON it carries
    wire = len(gzip.compress(plain.content))
    assert wire < len(plain.content) / 3
    # small answers are not worth compressing
    assert "content-encoding" not in client.get("/api/health", headers={"Accept-Encoding": "gzip"}).headers


# ------------------------------------------------------------------ rate-limit headers
def test_rate_limit_headers_count_down(client, base_request):
    first = client.post("/api/trips/preview", json=base_request)
    second = client.post("/api/trips/preview", json=base_request)
    assert first.headers["ratelimit-limit"] == "6"
    assert int(second.headers["ratelimit-remaining"]) < int(first.headers["ratelimit-remaining"])
    assert int(first.headers["ratelimit-reset"]) > 0
    for _ in range(10):
        r = client.post("/api/trips/preview", json=base_request)
        if r.status_code == 429:
            break
    assert r.status_code == 429 and r.headers["ratelimit-remaining"] == "0" and int(r.headers["retry-after"]) >= 1


def test_rate_limit_headers_are_readable_from_another_origin(client):
    r = client.options("/api/trips/preview", headers={"Origin": "http://localhost:5173",
                                                       "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200
    get = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    exposed = get.headers["access-control-expose-headers"].lower()
    assert "ratelimit-remaining" in exposed and "x-request-id" in exposed


# ------------------------------------------------------------------ request size limit
def test_oversized_bodies_get_413(client, base_request):
    from app.main import settings

    big = {**base_request, "destination_city": "x" * (settings.max_request_bytes + 10)}
    r = client.post("/api/trips/preview", json=big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    # a chunked body announces no length: it is counted as it arrives
    def chunks():
        for _ in range(settings.max_request_bytes // 1000 + 5):
            yield b" " * 1000

    r = client.post("/api/nlp/parse-trip", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    # a normal request is untouched
    assert client.post("/api/trips/preview", json=base_request).status_code == 200


# ------------------------------------------------------------------ outbound: retries and circuit breaker
@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(open_http.time, "sleep", lambda s: slept.append(s))
    return slept


def _client(handler) -> OpenHttp:
    return OpenHttp(Settings(data_mode="open"), transport=httpx.MockTransport(handler))


def test_a_short_failure_is_retried_once_with_backoff(no_sleep):
    calls = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"ok": True})

    assert _client(flaky).get_json("https://svc.test/a", what="Test") == {"ok": True}
    assert len(calls) == 2 and len(no_sleep) == 1 and 0 < no_sleep[0] <= 3


def test_a_dropped_connection_is_retried(no_sleep):
    calls = []

    def drop_then_ok(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json=[1])

    assert _client(drop_then_ok).get_json("https://svc.test/a", what="Test") == [1]


def test_client_errors_and_timeouts_are_not_retried(no_sleep):
    calls = []

    def not_found(request):
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(ProviderRequestError):
        _client(not_found).get_json("https://svc.test/a", what="Test")
    assert len(calls) == 1

    def slow(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    calls.clear()
    with pytest.raises(ProviderRequestError):
        _client(slow).get_json("https://svc.test/a", what="Test")
    assert len(calls) == 1 and not no_sleep


def test_a_long_retry_after_is_not_waited_for(no_sleep):
    calls = []

    def busy(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "120"})

    with pytest.raises(ProviderRequestError):
        _client(busy).get_json("https://svc.test/a", what="Test")
    assert len(calls) == 1 and not no_sleep


def test_the_breaker_skips_a_failing_host_then_tries_again():
    b = CircuitBreaker(threshold=3, cooldown=60)
    for _ in range(3):
        assert b.allow("svc", now=0)
        b.failure("svc", now=0)
    assert not b.allow("svc", now=30)          # open: refused without a request
    assert b.allow("svc", now=61)              # half-open: one trial
    assert not b.allow("svc", now=62)          # the others wait for its answer
    b.success("svc")
    assert b.allow("svc", now=63)              # closed again
    assert b.allow("other", now=0)             # hosts are independent


def test_an_open_breaker_fails_fast_without_a_request(no_sleep):
    calls = []

    def down(request):
        calls.append(1)
        return httpx.Response(502)

    http = _client(down)
    for _ in range(http.breaker.threshold):
        with pytest.raises(ProviderRequestError):
            http.get_json("https://svc.test/a", what="Test", retries=0)
    before = len(calls)
    with pytest.raises(ProviderRequestError, match="skipped for now"):
        http.get_json("https://svc.test/b", what="Test")
    assert len(calls) == before
