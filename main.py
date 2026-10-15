import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

EMAIL = os.environ.get("EMAIL", "your-email@example.com")
ASSIGNED_ORIGIN = "https://app-snncho.example.com"
# Comma-separated extra origins (the exam page's origin), set in Render env vars
EXTRA_ORIGINS = [o.strip() for o in os.environ.get("EXTRA_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = [ASSIGNED_ORIGIN, *EXTRA_ORIGINS]

LIMIT = 12      # requests
WINDOW = 10.0   # seconds

app = FastAPI()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limiter, bucketed per X-Client-Id."""

    def __init__(self, app):
        super().__init__(app)
        self.hits = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        client_id = request.headers.get("X-Client-Id") or (
            request.client.host if request.client else "anonymous"
        )
        now = time.monotonic()
        q = self.hits[client_id]
        while q and now - q[0] >= WINDOW:
            q.popleft()

        if len(q) >= LIMIT:
            retry = max(1, int(WINDOW - (now - q[0])) + 1)
            return JSONResponse(
                {"detail": "Too Many Requests"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        q.append(now)
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


# Starlette: the LAST added middleware is the OUTERMOST.
# Order of execution: CORS -> RequestContext -> RateLimit -> route
# so 429s also carry the request id and CORS headers.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # explicit list, no wildcard
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["X-Request-ID", "X-Client-Id", "Content-Type"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


@app.get("/ping")
async def ping(request: Request):
    return {"email": EMAIL, "request_id": request.state.request_id}
