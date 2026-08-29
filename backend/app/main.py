"""
RecoverOS Control Plane FastAPI Application.

Prototype control plane designed with production safety principles.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

# Ensure PROJECT_ROOT and BACKEND_ROOT are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.app.api.routes_recovery import router as recovery_router
from backend.app.api.routes_webhooks import router as webhooks_router
from backend.app.api.routes_dashboard import router as dashboard_router
from backend.app.core.config import ALLOWED_ORIGINS, PROJECT_NAME, VERSION
from backend.app.core.dependencies import get_decision_engine
from backend.app.models.database import init_db, SessionLocal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle management."""
    logger.info("Initializing RecoverOS Control Plane...")

    # 1. Initialize SQLite database schema
    init_db()
    logger.info("SQLite Event Ledger initialized.")

    # 2. Verify model artifact availability
    engine = get_decision_engine()
    if getattr(engine, 'diagnosis_engine', None) is not None:
        logger.info(f"DiagnosisEngine loaded successfully ({engine.model_version}).")
    else:
        logger.warning("DiagnosisEngine unavailable. Operating in safe degraded fallback mode.")

    yield

    logger.info("Shutting down RecoverOS Control Plane.")


app = FastAPI(
    title=PROJECT_NAME,
    version=VERSION,
    description="Prototype control plane designed with production safety principles for subscription payment failure recovery.",
    lifespan=lifespan,
)

# CORS Middleware
# NOTE: The webhook endpoint (/v1/webhooks/razorpay) is server-to-server.
# Its security is enforced by HMAC-SHA256 signature verification, NOT CORS.
# CORS here applies only to browser-facing endpoints (docs, decision API, health).
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# Mount API Routers
app.include_router(recovery_router)
app.include_router(webhooks_router)
app.include_router(dashboard_router)


@app.get("/", tags=["System"])
def root_info():
    """Root endpoint returning service identity and version."""
    return {
        "service": PROJECT_NAME,
        "version": VERSION,
        "status": "online",
        "documentation": "/docs",
    }


@app.get("/health", tags=["System"], status_code=status.HTTP_200_OK)
def health_check():
    """Health check endpoint verifying system, database, and model status."""
    engine = get_decision_engine()
    # Verify actual database connectivity — do not report healthy if DB is inaccessible.
    db_status = "sqlite_connected"
    db_ok = True
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as e:
        db_status = f"error: {type(e).__name__}"
        db_ok = False
        logger.error(f"Health check: database connectivity failed: {e}")

    http_status = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    from fastapi import Response
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=http_status,
        content={
            "status": "healthy" if db_ok else "unhealthy",
            "database": db_status,
            "diagnosis_engine_loaded": getattr(engine, 'diagnosis_engine', None) is not None,
            "model_version": engine.model_version,
            "version": VERSION,
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
