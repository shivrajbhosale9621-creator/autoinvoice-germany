from fastapi import Request
from fastapi.responses import JSONResponse

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    limiter = Limiter(key_func=get_remote_address, default_limits=[])
except ImportError:
    class RateLimitExceeded(Exception):
        def __init__(self, detail: str = "Rate limit exceeded"):
            self.detail = detail
            super().__init__(detail)

    class _FallbackLimiter:
        def limit(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    limiter = _FallbackLimiter()


def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return uniform JSON error for rate limit violations."""
    return JSONResponse(
        status_code=429,
        content={
            "status": "error",
            "error": "Too many requests. Please try again later.",
            "detail": getattr(exc, "detail", "Rate limit exceeded"),
        },
    )
