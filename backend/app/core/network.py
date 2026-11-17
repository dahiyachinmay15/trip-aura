"""HTTP-level middleware: request ids and timing, a request size limit, and caching rules with ETags.

All three are plain ASGI middleware (no per-request task or body copy, unlike BaseHTTPMiddleware):

- **Request ids.** Every response carries `X-Request-ID` (a safe id sent by a proxy is kept, otherwise one is made)
  and `Server-Timing: app;dur=<ms>`, so one request can be followed through Vercel, Azure and the logs. Errors and
  slow requests are logged with the id; the path is logged without its query string, which can hold what a
  person typed.
- **Size limit.** A body larger than `MAX_REQUEST_BYTES` gets 413, whether the client announced its length or sent
  it in chunks.
- **Caching.** API responses are `no-store` by default: a trip plan or an address search is personal and must not be
  kept by a shared cache. A few public, deterministic GET endpoints are cacheable and get a weak ETag; a request
  whose `If-None-Match` matches gets `304 Not Modified` with no body.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

log = logging.getLogger("tripaura.http")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
SAFE_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
SLOW_MS = 3000   # requests slower than this are logged at INFO even when they succeed

# path -> Cache-Control for successful GETs; every other API response is "no-store"
CACHE_RULES: dict[str, str] = {
    "/api/meta/options": "public, max-age=3600",       # the form's choices: change only with a release
    "/api/geo/cities": "public, max-age=86400",        # the offline GeoNames index: same answer for the same query
    "/api/meta/status": "no-cache",                    # may be stored, but must be revalidated (cheap with the ETag)
}


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


async def _send_error(send: Send, status: int, code: str, message: str, extra: list[tuple[bytes, bytes]]) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "details": {}}}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store"), *extra]})
    await send({"type": "http.response.body", "body": body})


class RequestContextMiddleware:
    """Request id, Server-Timing, the body size limit and logging of failed or slow requests."""

    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, b"x-request-id") or ""
        rid = incoming if SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        id_header = (b"x-request-id", rid.encode())
        state = {"status": 0, "too_large": False, "replaced": False}

        try:
            length = _header(scope, b"content-length")
            if length is not None and length.isdigit() and int(length) > self.max_body:
                state["status"] = 413
                await _send_error(send, 413, "payload_too_large",
                                  f"The request body is larger than {self.max_body // 1024} kB.", [id_header])
                return

            received = 0

            async def limited_receive() -> Message:
                # a chunked body has no length up front: count it, and stop reading once it is too large
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > self.max_body:
                        state["too_large"] = True
                        return {"type": "http.request", "body": b"", "more_body": False}
                return message

            async def send_wrapper(message: Message) -> None:
                if state["too_large"]:
                    # the app answered a cut-off body (usually 422): replace that answer with 413
                    if message["type"] == "http.response.start" and not state["replaced"]:
                        state["replaced"], state["status"] = True, 413
                        await _send_error(send, 413, "payload_too_large",
                                          f"The request body is larger than {self.max_body // 1024} kB.", [id_header])
                    return
                if message["type"] == "http.response.start":
                    state["status"] = message["status"]
                    ms = (time.perf_counter() - started) * 1000
                    headers = list(message.get("headers", []))
                    headers.append(id_header)
                    headers.append((b"server-timing", f"app;dur={ms:.1f}".encode()))
                    message["headers"] = headers
                await send(message)

            try:
                await self.app(scope, limited_receive, send_wrapper)
            except Exception:
                # an unexpected error: answer with the API's envelope and the id, which Starlette's own error page
                # (outside this middleware) could not carry; if the answer had already begun, there is nothing to add
                log.exception("Unhandled error in %s %s [%s]", scope.get("method"), scope.get("path"), rid)
                if state["status"]:
                    raise
                state["status"] = 500
                body = json.dumps({"error": {"code": "internal_error", "message": "Unexpected server error",
                                             "details": {"request_id": rid}}}).encode()
                await send({"type": "http.response.start", "status": 500,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode()),
                                        (b"cache-control", b"no-store"), id_header]})
                await send({"type": "http.response.body", "body": body})
        finally:
            ms = (time.perf_counter() - started) * 1000
            status = state["status"]
            level = logging.WARNING if status >= 500 or status == 0 else (
                logging.INFO if ms > SLOW_MS or status in (413, 429) else logging.DEBUG)
            log.log(level, "%s %s -> %s in %.0f ms [%s]", scope.get("method"), scope.get("path"), status or "error",
                    ms, rid)
            request_id.reset(token)


def _etag(body: bytes) -> str:
    # weak: the same data may also be sent gzip-compressed, which is a different byte sequence
    return 'W/"' + hashlib.sha256(body).hexdigest()[:24] + '"'


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return any(tag.strip().removeprefix("W/") == bare for tag in header.split(","))


class HttpCacheMiddleware:
    """Cache-Control for every API response; ETags and 304s for the cacheable GETs in CACHE_RULES."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        rule = CACHE_RULES.get(path) if scope.get("method") in ("GET", "HEAD") else None

        if rule is None:
            async def no_store(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    if not any(k == b"cache-control" for k, _ in headers):
                        headers.append((b"cache-control", b"no-store"))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, no_store)
            return

        # cacheable: hold the (small) response, then answer with it or with 304
        start: Message | None = None
        chunks: list[bytes] = []

        async def collect(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, collect)
        if start is None:
            return
        body = b"".join(chunks)
        headers = [(k, v) for k, v in start.get("headers", []) if k not in (b"cache-control", b"etag")]
        if start["status"] != 200:
            await send({**start, "headers": headers + [(b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return
        etag = _etag(body)
        headers += [(b"cache-control", rule.encode()), (b"etag", etag.encode())]
        if _etag_matches(_header(scope, b"if-none-match"), etag):
            # 304: same validators, no body and no body headers
            headers = [(k, v) for k, v in headers if k not in (b"content-length", b"content-type")]
            await send({"type": "http.response.start", "status": 304, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body})
