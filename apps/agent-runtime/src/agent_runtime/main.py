from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_runtime.api.events import build_run_event_service
from agent_runtime.api.events import router as events_router
from agent_runtime.api.health import router as health_router
from agent_runtime.api.runs import build_run_admission_service
from agent_runtime.api.runs import router as runs_router
from agent_runtime.auth import close_shared_parent_session_verifier
from agent_runtime.llm.composition import model_gateway_resources
from agent_runtime.persistence.object_store import create_object_store
from agent_runtime.settings import get_settings


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    """Own and release process-wide authorization, object store, and model gateway resources."""
    settings = get_settings()
    object_store = create_object_store(settings)
    app_instance.state.object_store = object_store
    app_instance.state.run_admission_service = build_run_admission_service(
        settings=settings,
        object_store=object_store,
    )
    app_instance.state.run_event_service = build_run_event_service()
    try:
        async with model_gateway_resources(settings) as gateway:
            app_instance.state.model_gateway = gateway
            yield
    finally:
        app_instance.state.model_gateway = None
        app_instance.state.run_admission_service = None
        app_instance.state.run_event_service = None
        app_instance.state.object_store = None
        await object_store.aclose()
        close_shared_parent_session_verifier()


app = FastAPI(title="Agent Execution Runtime v2.3", version="2.3.0", lifespan=lifespan)

app.include_router(health_router)
app.include_router(runs_router)
app.include_router(events_router)
