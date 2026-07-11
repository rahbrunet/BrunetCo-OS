"""MCP server surface for the OS API (design review §4/§7.5).

Exposed from day one so every future Claude-based workflow (the agents themselves, ad-hoc
"ask about matter X" queries) is first-class rather than bespoke. In WP 0.7 it serves only the
health/demo tool; tools will be generated from the same OpenAPI schema as `packages/contracts`
so the MCP surface never drifts from the REST API.

Auth is deferred to a documented TODO tied to the D44 JWT bridge (py_shared.auth): MCP calls on
user paths must carry the same per-request Supabase JWT and go through `user_connection`, never
the service-role key.
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

mcp = FastMCP("brunetco-os")
# Serve at the mount root so mounting under FastAPI at "/mcp" yields the endpoint at "/mcp".
mcp.settings.streamable_http_path = "/"


@mcp.tool()
def health() -> dict[str, str]:
    """Demo tool — mirrors GET /api/v1/health. Proves the MCP server boots and serves."""
    return {"status": "ok", "service": "brunetco-mcp", "version": "0.7.0"}


def build_mcp_app() -> Starlette:
    """Return the MCP server as an ASGI app for mounting under the FastAPI app at /mcp.

    TODO(D44): wrap with auth middleware that requires the per-request Supabase JWT before any
    non-public tool is served.
    """
    return mcp.streamable_http_app()


def mcp_session_lifespan() -> AbstractAsyncContextManager[None]:
    """The MCP session manager context — must run for the mounted app to serve requests.

    Starlette does not auto-run a mounted sub-app's lifespan, so the FastAPI app wires this into
    its own lifespan (see main.py).
    """
    return mcp.session_manager.run()
