"""BrunetCo OS API — WP 0.7 scaffold.

Ships a single typed demo endpoint (GET /api/v1/health) to prove the OpenAPI -> typed-contract
-> SPA loop, plus a mounted MCP server (see mcp_server.py). No domain routes yet (WP 0.8).

The FastAPI schema is the source of truth for `packages/contracts` — regenerate with
`python make.py gen-contracts`. CI fails if the committed contracts drift from this schema.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.mcp_server import build_mcp_app, mcp_session_lifespan
from app.routes.audit import router as audit_router
from app.routes.conflicts import router as conflicts_router
from app.routes.docketing import router as docketing_router
from app.routes.families import router as families_router
from app.routes.matters import router as matters_router
from app.routes.my_day import router as my_day_router
from app.routes.orchestrator import router as orchestrator_router
from app.routes.permissions_admin import me_router
from app.routes.permissions_admin import router as permissions_router
from app.routes.prior_art import router as prior_art_router
from app.routes.rules import router as rules_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Run the mounted MCP server's session manager (Starlette won't run a sub-app lifespan for us).
    async with mcp_session_lifespan():
        yield


app = FastAPI(
    title="BrunetCo OS API",
    version="0.7.0",
    description="IP practice-management platform API (WP 0.7 scaffold).",
    lifespan=lifespan,
)

# Browser-native SPA (spec §2). Tighten origins per-environment in WP 0.8.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    """Typed health payload — consumed end-to-end by the SPA to prove the contract loop."""

    status: str
    service: str
    version: str


@app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="brunetco-api", version=app.version)


app.include_router(me_router)
app.include_router(permissions_router)
app.include_router(families_router)
app.include_router(matters_router)
app.include_router(docketing_router)
app.include_router(rules_router)
app.include_router(orchestrator_router)
app.include_router(conflicts_router)
app.include_router(my_day_router)
app.include_router(prior_art_router)
app.include_router(audit_router)

# MCP surface from day one (design review §4/§7.5), derived from this same app.
# Auth is a documented TODO tied to the D44 JWT bridge — see mcp_server.py.
app.mount("/mcp", build_mcp_app())
