"""FastAPI transport boundary for asynchronous Personal Agent Runs."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, status

from personal_agent.application.contracts import RunResponse, SubmitMessage
from personal_agent.application.runs import (
    PersonalRun,
    PersonalRunScheduler,
    PersonalRunStore,
    QueueFullError,
    RunConflictError,
)
from personal_agent.contracts import HealthResponse


def _run_response(run: PersonalRun) -> RunResponse:
    return RunResponse(
        run_id=run.run_id,
        status=run.status.value,
        answer=run.answer,
        error_message=run.error_message,
    )


def create_app(
    *,
    scheduler: PersonalRunScheduler | None = None,
    run_store: PersonalRunStore | None = None,
) -> FastAPI:
    """Create an HTTP app around injected scheduling dependencies."""

    if (scheduler is None) != (run_store is None):
        raise ValueError("scheduler 与 run_store 必须同时提供")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if scheduler is not None:
            await scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                await scheduler.stop()

    app = FastAPI(title="Personal Agent API", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.post(
        "/api/conversations/{conversation_id}/messages",
        response_model=RunResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["conversations"],
    )
    async def submit_message(conversation_id: str, request: SubmitMessage) -> RunResponse:
        if scheduler is None:
            raise HTTPException(status_code=503, detail="Agent 服务尚未配置")
        try:
            run = await scheduler.submit(conversation_id, request.question.strip())
        except RunConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except QueueFullError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        return _run_response(run)

    @app.get("/api/runs/{run_id}", response_model=RunResponse, tags=["runs"])
    async def get_run(run_id: str) -> RunResponse:
        if run_store is None:
            raise HTTPException(status_code=503, detail="Agent 服务尚未配置")
        run = run_store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="未找到 Run")
        return _run_response(run)

    return app


app = create_app()
