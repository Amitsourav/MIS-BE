"""MIS backend FastAPI application."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app import __version__
from app.api import admin, auth, provider
from app.config import settings
from app.core import timing
from app.core.ratelimit import limiter
from app.crm.chat import ChatError, ChatUnavailable
from app.crm.invoices import InvoiceError
from app.deps import forget_logins
from app.services.scorecard import forget_targets
from app.crm.pool import CrmUnavailable, close_pools

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MIS backend starting (env=%s, data=live from CRMs)", settings.env)
    try:
        yield
    finally:
        await close_pools()
        logger.info("MIS backend stopped")


app = FastAPI(
    title="MIS Lead-Provider Portal API",
    version=__version__,
    lifespan=lifespan,
)

# Rate limiting (slowapi)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# A CRM that can't be reached is a temporary outage, not a server bug.
@app.exception_handler(CrmUnavailable)
async def _crm_unavailable(_: Request, exc: CrmUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ChatUnavailable)
async def _chat_unavailable(_: Request, exc: ChatUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# The CRM's 404 / 422 / 429 on a chat call, passed on to the partner.
@app.exception_handler(ChatError)
async def _chat_error(_: Request, exc: ChatError) -> JSONResponse:
    detail = "Lead not found" if exc.status == 404 else exc.detail
    return JSONResponse(status_code=exc.status, content={"detail": detail})


# The CRM's 404 / 409 / 422 / 429 on an invoice call: its detail is written
# for the partner, so it passes through as-is.
@app.exception_handler(InvoiceError)
async def _invoice_error(_: Request, exc: InvoiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Server-Timing on every response (outermost, so it covers the whole request).
@app.middleware("http")
async def _server_timing(request: Request, call_next):
    timing.start()
    t = time.perf_counter()
    response = await call_next(request)
    response.headers["Server-Timing"] = timing.header(time.perf_counter() - t)
    return response


# Partner logins and scorecard targets are cached briefly (app/deps.py,
# app/services/scorecard.py); any admin change — a password reset,
# deactivation, source mapping, new targets — must apply at once.
@app.middleware("http")
async def _forget_caches_on_admin_write(request: Request, call_next):
    response = await call_next(request)
    if request.method != "GET" and request.url.path.startswith("/admin/"):
        forget_logins()
        forget_targets()
    return response


# Routers
app.include_router(auth.router)
app.include_router(provider.router)
app.include_router(provider.payouts_router)
app.include_router(admin.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": __version__}
