import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routers import contacts, opportunities, resource_estimation, resources
from services import opportunities as opp_service
import config
from routers import agent, tasks

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting backend for Glean instance %s", config.GLEAN_INSTANCE)
    opp_service.manager.start()
    yield
    opp_service.manager.stop()

def create_app() -> FastAPI:
    app = FastAPI(
        title="Glean Agent Backend",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )

    app.include_router(agent.router)
    app.include_router(opportunities.router, prefix="/api/v1")
    app.include_router(resource_estimation.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(contacts.router, prefix="/api/v1")
    app.include_router(resources.router, prefix="/api/v1")

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app

app = create_app()
