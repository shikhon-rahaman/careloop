"""
CareLoop - Streamable HTTP MCP & Web Server (server.py)

Exposes the 5 CareLoop tools as MCP tools using the official MCP Python SDK (FastMCP / MCPServer)
over Streamable HTTP (MCP spec 2025-11-25+), mounted at /mcp inside a Starlette application.

Web Routes:
  - POST /api/chat      : Elder conversational chat endpoint (routes through intents.py)
  - GET  /api/dashboard : Health adherence state, medication logs, and alerts JSON
  - POST /api/reset     : Clears daily logs/alerts while preserving medication schedule
  - GET  /              : Serves static web dashboard from static/
  - POST /mcp           : Streamable HTTP MCP transport endpoint
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
import uvicorn

# Official MCP Python SDK imports (SDK v2 MCPServer with FastMCP alias)
try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.fastmcp import FastMCP  # type: ignore

from mcp.server.transport_security import TransportSecuritySettings

import tools
from db import get_alerts, get_med_logs, get_schedule, get_wellness_logs, init_db, reset_db
from intents import handle_message

# ---------------------------------------------------------------------------
# FastMCP Server Initialization & Tool Declarations
# ---------------------------------------------------------------------------

mcp = FastMCP("CareLoop")


@mcp.tool(
    name="log_medication",
    description=(
        "Log a patient's medication intake event. Uses intelligent fuzzy matching "
        "to pair colloquial names (e.g., 'bp pill') to scheduled medications (e.g., 'BP tablet'). "
        "If taken_at is omitted, the current timestamp is recorded automatically."
    ),
)
def log_medication(name: str, taken_at: str | None = None) -> dict[str, Any]:
    """Log medication intake with schedule fuzzy-matching."""
    return tools.log_medication(name=name, taken_at=taken_at)


@mcp.tool(
    name="log_wellness",
    description=(
        "Record an elder wellness check-in with mood and symptoms. If high-risk symptoms "
        "(dizzy, fell, fall, chest pain, can't breathe, bleeding) are reported, an immediate "
        "high-severity alert is dispatched to family."
    ),
)
def log_wellness(mood: str, symptoms: str = "") -> dict[str, Any]:
    """Record patient wellness check-in and trigger safety alerts if critical symptoms appear."""
    return tools.log_wellness(mood=mood, symptoms=symptoms)


@mcp.tool(
    name="get_daily_summary",
    description=(
        "Retrieve today's complete health summary for the patient, including medications "
        "taken, missed medications whose scheduled time has passed, recorded mood entries, "
        "and all family alerts."
    ),
)
def get_daily_summary() -> dict[str, Any]:
    """Retrieve today's complete health summary (taken, missed, mood, alerts)."""
    return tools.get_daily_summary()


@mcp.tool(
    name="check_missed_meds",
    description=(
        "Check for scheduled medications whose scheduled intake time has already passed today "
        "without any recorded intake log."
    ),
)
def check_missed_meds() -> dict[str, Any]:
    """Identify scheduled medications missed today."""
    return tools.check_missed_meds()


@mcp.tool(
    name="alert_family",
    description=(
        "Dispatch an urgent alert notification to family members (severity: 'low', 'medium', "
        "or 'high'). The alert is recorded in the SQLite database and logged to the console."
    ),
)
def alert_family(reason: str, severity: str) -> dict[str, Any]:
    """Send family alert with severity level (low|medium|high)."""
    return tools.alert_family(reason=reason, severity=severity)


# ---------------------------------------------------------------------------
# Starlette Web Routes & ASGI Handlers
# ---------------------------------------------------------------------------

async def chat_endpoint(request: Request) -> JSONResponse:
    """Handle elder conversational messages via rule-based intent router."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = payload.get("message", "")
    result = handle_message(message)
    return JSONResponse(result)


async def dashboard_endpoint(request: Request) -> JSONResponse:
    """Return JSON state of today's schedule, intake adherence, wellness logs, and alerts."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    summary = tools.get_daily_summary(date_str=today_str)
    schedule = get_schedule()
    med_logs = get_med_logs(date_str=today_str)
    wellness_logs = get_wellness_logs(date_str=today_str)
    alerts = get_alerts(date_str=today_str)

    return JSONResponse(
        {
            "date": today_str,
            "summary": summary,
            "schedule": schedule,
            "med_logs": med_logs,
            "wellness_logs": wellness_logs,
            "alerts": alerts,
        }
    )


async def reset_endpoint(request: Request) -> JSONResponse:
    """Clear today's logs and alerts while preserving medication schedule."""
    reset_db()
    return JSONResponse(
        {
            "status": "success",
            "message": "Logs and alerts cleared, medication schedule preserved.",
        }
    )


class DirectASGI:
    """ASGI forwarder that normalizes path for exact '/mcp' route requests."""

    def __init__(self, subapp: Any) -> None:
        self.subapp = subapp

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope_copy = dict(scope)
        scope_copy["path"] = "/"
        await self.subapp(scope_copy, receive, send)


# ---------------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------------

def create_app() -> Starlette:
    """Construct Starlette application mounting Streamable HTTP MCP and web routes."""
    # Transport security configured to allow local and remote connections (e.g. MCP Inspector)
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    # Instantiate Streamable HTTP MCP sub-application targeting MCP spec 2025-11-25+
    mcp_subapp = mcp.streamable_http_app(
        streamable_http_path="/",
        transport_security=security,
    )

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # Initialize SQLite database and seed baseline schedule on startup
        init_db()
        async with mcp.session_manager.run():
            yield

    routes = [
        Route("/api/chat", endpoint=chat_endpoint, methods=["POST"]),
        Route("/api/dashboard", endpoint=dashboard_endpoint, methods=["GET"]),
        Route("/api/reset", endpoint=reset_endpoint, methods=["POST"]),
        # Support both /mcp and /mcp/ without HTTP 307 redirects
        Route("/mcp", endpoint=DirectASGI(mcp_subapp), methods=["GET", "POST", "DELETE"]),
        Mount("/mcp", app=mcp_subapp),
        # Serve static assets and web dashboard at /
        Mount("/", app=StaticFiles(directory=str(static_dir), html=True)),
    ]

    return Starlette(
        debug=True,
        routes=routes,
        lifespan=lifespan,
    )


app = create_app()


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    print("=" * 65)
    print(f" CareLoop Server - Starting on http://{host}:{port}")
    print(f" - Web Dashboard    : http://localhost:{port}/")
    print(f" - MCP Endpoint     : http://localhost:{port}/mcp (Streamable HTTP)")
    print(f" - API Chat         : http://localhost:{port}/api/chat (POST)")
    print(f" - API Dashboard    : http://localhost:{port}/api/dashboard (GET)")
    print(f" - API Reset        : http://localhost:{port}/api/reset (POST)")
    print("=" * 65)
    uvicorn.run(app, host=host, port=port)
