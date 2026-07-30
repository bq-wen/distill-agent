"""FastAPI application factory; Agent execution is added in a later increment."""

from fastapi import FastAPI

from personal_agent.contracts import HealthResponse


def create_app() -> FastAPI:
    app = FastAPI(title="Personal Agent API", version="0.1.0")

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


app = create_app()
