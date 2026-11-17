"""HTTP security middleware: response headers, Origin checks on unsafe requests, per-IP rate limits."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.errors import CsrfError, RateLimitedError, TripAuraError
from app.core.ratelimit import client_ip, ip_route_class, limiter

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DOCS_PATHS = ("/docs", "/redoc")

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
# Swagger UI and ReDoc load their bundles from jsDelivr and run a small inline bootstrap script.
DOCS_CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com "
            "https://cdn.redoc.ly; connect-src 'self'; worker-src blob:; "
            "frame-ancestors 'none'; base-uri 'none'")
STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # API answers are data for this site's own pages, never something another site may embed
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
}


def error_response(status: int, code: str, message: str, details: dict | list | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    """The API's error envelope: {"error": {"code", "message", "details"}}."""
    body = {"code": code, "message": message, "details": {} if details is None else details}
    return JSONResponse(status_code=status, headers=headers or None, content={"error": body})


def exception_response(exc: TripAuraError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


def is_https(request: Request, settings: Settings) -> bool:
    if request.url.scheme == "https":
        return True
    if settings.trust_proxy_headers:
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        return proto == "https"
    return False


def origin_allowed(origin: str, request: Request, settings: Settings) -> bool:
    """Origin is in CORS_ORIGINS, matches CORS_ORIGIN_REGEX, or is the API's own host."""
    origin = origin.strip().rstrip("/")
    if origin in settings.allowed_origins:
        return True
    if settings.cors_origin_regex and re.fullmatch(settings.cors_origin_regex, origin):
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    hosts = {request.headers.get("host", "").lower()}
    if settings.trust_proxy_headers and request.headers.get("x-forwarded-host"):
        hosts.add(request.headers["x-forwarded-host"].split(",")[0].strip().lower())
    return parts.netloc.lower() in hosts


async def security_headers(request: Request, call_next):
    settings = get_settings()
    response = await call_next(request)
    for name, value in STATIC_HEADERS.items():
        response.headers.setdefault(name, value)
    path = request.url.path
    is_docs = path in DOCS_PATHS or path.startswith("/docs/")
    response.headers["Content-Security-Policy"] = DOCS_CSP if is_docs else API_CSP
    if is_https(request, settings):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


async def request_guard(request: Request, call_next):
    """Rejects cross-site unsafe requests from unknown origins, then applies the per-IP rate limit.

    Runs inside the CORS middleware, so preflight (OPTIONS) requests never reach it. Errors raised in
    middleware bypass the app's exception handlers, so the error responses are built here."""
    settings = get_settings()
    method = request.method.upper()
    if method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and not origin_allowed(origin, request, settings):
            return exception_response(CsrfError("Request origin is not allowed"))
    if settings.rate_limit_enabled and method != "OPTIONS":
        quota = limiter.take(ip_route_class(method, request.url.path), client_ip(request, settings))
        # RateLimit-* (IETF draft, as most APIs send them): how many requests are left and when the quota refills
        quota_headers = {"RateLimit-Limit": str(quota.limit), "RateLimit-Remaining": str(quota.remaining),
                         "RateLimit-Reset": str(quota.reset)}
        if quota.wait:
            response = exception_response(RateLimitedError(quota.wait))
        else:
            response = await call_next(request)
        response.headers.update(quota_headers)
        return response
    return await call_next(request)
