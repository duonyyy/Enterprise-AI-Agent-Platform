import asyncio
import time
from fastapi import FastAPI, Response, status
from sqlalchemy import text
from control_api.core.database import engine
from control_api.core.settings import settings
from control_api.modules.agents.api.router import router as agents_router
from control_api.modules.approvals.router import router as approvals_router
from control_api.modules.skills.api.router import router as skills_router
from control_api.modules.mcp.api.router import router as mcp_router

app = FastAPI(title=settings.app_name, version="2.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/live")
def health_live():
    return {"status": "alive", "app": settings.app_name, "timestamp": time.time()}


@app.get("/health/ready")
async def health_ready(response: Response):
    start = time.perf_counter()
    try:
        async def _check_db() -> bool:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                return result.scalar() == 1

        is_ok = await asyncio.wait_for(_check_db(), timeout=1.5)
        latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
        if is_ok:
            response.status_code = status.HTTP_200_OK
            return {
                "status": "ready",
                "app": settings.app_name,
                "timestamp": time.time(),
                "components": [
                    {
                        "name": "database_postgres",
                        "status": "ok",
                        "latency_ms": latency_ms,
                        "is_critical": True,
                        "message": None,
                    }
                ],
            }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "app": settings.app_name,
            "timestamp": time.time(),
            "components": [
                {
                    "name": "database_postgres",
                    "status": "failed",
                    "latency_ms": latency_ms,
                    "is_critical": True,
                    "message": f"Database readiness check failed: {type(exc).__name__}",
                }
            ],
        }

    latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "not_ready",
        "app": settings.app_name,
        "timestamp": time.time(),
        "components": [
            {
                "name": "database_postgres",
                "status": "failed",
                "latency_ms": latency_ms,
                "is_critical": True,
                "message": "Database probe returned unexpected result",
            }
        ],
    }


app.include_router(agents_router)
app.include_router(approvals_router)
app.include_router(skills_router)
app.include_router(mcp_router)
