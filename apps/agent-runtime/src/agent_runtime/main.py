from fastapi import FastAPI
from agent_runtime.api.runs import router as runs_router
from agent_runtime.api.events import router as events_router
from agent_runtime.api.health import router as health_router

app = FastAPI(title="Agent Execution Runtime v2.0", version="2.0.0")

app.include_router(health_router)
app.include_router(runs_router)
app.include_router(events_router)
