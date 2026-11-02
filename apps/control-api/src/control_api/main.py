from fastapi import FastAPI
from control_api.core.settings import settings
from control_api.modules.agents.api.router import router as agents_router
from control_api.modules.approvals.router import router as approvals_router
from control_api.modules.skills.api.router import router as skills_router
from control_api.modules.mcp.api.router import router as mcp_router

app = FastAPI(title=settings.app_name, version="2.0.0")

@app.get("/health")
def health(): return {"status": "ok", "app": settings.app_name}

app.include_router(agents_router)
app.include_router(approvals_router)
app.include_router(skills_router)
app.include_router(mcp_router)
