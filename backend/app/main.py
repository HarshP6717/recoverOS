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

# Ensure PROJECT_ROOT and BACKEND_ROOT are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.app.api.routes_recovery import router as recovery_router
from backend.app.api.routes_webhooks import router as webhooks_router
from backend.app.core.config import PROJECT_NAME, VERSION
from backend.app.core.dependencies import get_decision_engine
from backend.app.models.database import init_db

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
    if engine.model is not None:
        logger.info(f"Phase-1 ML Model loaded successfully ({engine.model_version}).")
    else:
        logger.warning("Phase-1 ML Model artifact unavailable. Operating in safe degraded fallback mode.")

    yield

    logger.info("Shutting down RecoverOS Control Plane.")


app = FastAPI(
    title=PROJECT_NAME,
    version=VERSION,
    description="Prototype control plane designed with production safety principles for subscription payment failure recovery.",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(recovery_router)
app.include_router(webhooks_router)


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
    """Health check endpoint indicating system and model status."""
    engine = get_decision_engine()
    return {
        "status": "healthy",
        "database": "sqlite_connected",
        "model_loaded": engine.model is not None,
        "model_version": engine.model_version,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
