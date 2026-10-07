"""MIS backend FastAPI application."""
from __future__ import annotations

import logging
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
from app.core.ratelimit import limiter
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


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(provider.router)
app.include_router(provider.payouts_router)
app.include_router(admin.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": __version__}
