"""Per-request context (correlation id, client IP) available to services and audit logging."""
import contextvars
import re
import uuid

_request_context = contextvars.ContextVar("request_context", default=None)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def get_request_context():
    return _request_context.get() or {}


def _client_ip(request):
    # Only REMOTE_ADDR is trusted; proxies in front of Django should rewrite it.
    return request.META.get("REMOTE_ADDR") or None


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = _request_context.set({"request_id": request_id, "ip_address": _client_ip(request)})
        try:
            response = self.get_response(request)
        finally:
            _request_context.reset(token)
        response["X-Request-ID"] = request_id
        return response
