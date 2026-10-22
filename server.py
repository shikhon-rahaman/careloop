"""
CareLoop - Streamable HTTP MCP & Web Server (server.py)

Exposes the 5 CareLoop tools as MCP tools using the official MCP Python SDK (FastMCP / MCPServer)
over Streamable HTTP (MCP spec 2025-11-25+), mounted at /mcp inside a Starlette application.

The /api/chat endpoint acts as a real MCP client:
  1. Uses intents.py strictly to determine the target tool and arguments.
  2. Connects to this server's own /mcp endpoint via the official MCP SDK client (streamable_http_client).
  3. Calls session.call_tool(tool, arguments) and returns the formatted response.
  4. Records the last 10 MCP tool executions in an in-memory log.

Web Routes:
  - POST /api/chat      : Elder conversational chat endpoint (acting as MCP client)
  - GET  /api/dashboard : Health adherence state, medication logs, alerts, and MCP log JSON
  - POST /api/reset     : Clears daily logs/alerts while preserving medication schedule
  - GET  /              : Serves static web dashboard from static/
  - POST /mcp           : Streamable HTTP MCP transport endpoint
"""

from __future__ import annotations

import json
import os
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

# Official MCP Python SDK server imports (SDK v2 MCPServer with FastMCP alias)
try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.fastmcp import FastMCP  # type: ignore

from mcp.server.transport_security import TransportSecuritySettings

# Official MCP Python SDK client imports
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

import tools
from db import get_alerts, get_med_logs, get_schedule, get_wellness_logs, init_db, reset_db
from intents import FALLBACK_REPLY, format_reply, match_intent

# In-memory history of the last 10 MCP tool calls
mcp_call_history: list[dict[str, Any]] = []

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

async def _invoke_mcp_client(
    endpoint_url: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Connect to this server's own Streamable HTTP /mcp endpoint using the official
    MCP Python SDK client (streamable_http_client + ClientSession).
    """
    async with streamable_http_client(endpoint_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            call_res = await session.call_tool(tool_name, arguments)

            # Extract structured result
            if hasattr(call_res, "structured_content") and call_res.structured_content:
                return call_res.structured_content
            if call_res.content:
                first = call_res.content[0]
                if hasattr(first, "text") and first.text:
                    try:
                        return json.loads(first.text)
                    except Exception:
                        return {"raw": first.text}
            return {}


async def chat_endpoint(request: Request) -> JSONResponse:
    """
    Elder conversational chat endpoint.
    Acts as a real MCP client:
      - Uses intents.match_intent() only to determine tool & arguments.
      - Connects via official MCP SDK client to /mcp.
      - Invokes session.call_tool() and returns result + formatted mcp_call string.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    message = payload.get("message", "")
    intent = match_intent(message)

    tool_name = intent.get("tool")
    arguments = intent.get("arguments", {})

    # If no tool matched, return fallback guidance
    if not tool_name:
        return JSONResponse(
            {
                "reply": intent.get("fallback_reply") or FALLBACK_REPLY,
                "tool": None,
                "arguments": {},
                "mcp_call": None,
                "data": None,
                "mcp_logs": list(mcp_call_history),
            }
        )

    # Resolve local server port to call own /mcp endpoint
    port = request.url.port or int(os.environ.get("PORT", 8000))
    host = "127.0.0.1"
    mcp_endpoint_url = f"http://{host}:{port}/mcp"

    # Format human-readable call string: e.g. "MCP call: log_wellness(mood='Dizzy', symptoms='I feel dizzy')"
    arg_parts = [
        f"{k}='{v}'" if isinstance(v, str) else f"{k}={v}"
        for k, v in arguments.items()
    ]
    arg_str = ", ".join(arg_parts)
    mcp_call_str = f"MCP call: {tool_name}({arg_str})"

    now_hhmmss = datetime.now().strftime("%H:%M:%S")

    try:
        # Act as a real MCP client: call session.call_tool() over Streamable HTTP
        result_data = await _invoke_mcp_client(mcp_endpoint_url, tool_name, arguments)
        status = "success"
    except Exception as exc:
        print(f"[MCP Client Error] Failed to call {tool_name} over /mcp: {exc}")
        result_data = {"error": str(exc)}
        status = "error"

    # Construct friendly spoken response
    reply = format_reply(tool_name, arguments, result_data)

    # Append to last 10 MCP calls log
    log_entry = {
        "timestamp": now_hhmmss,
        "tool": tool_name,
        "arguments": arguments,
        "mcp_call": f"{tool_name}({arg_str})",
        "status": status,
        "preview": str(result_data)[:90],
    }
    mcp_call_history.append(log_entry)
    if len(mcp_call_history) > 10:
        mcp_call_history.pop(0)

    return JSONResponse(
        {
            "reply": reply,
            "tool": tool_name,
            "arguments": arguments,
            "mcp_call": mcp_call_str,
            "data": result_data,
            "mcp_logs": list(mcp_call_history),
        }
    )


async def dashboard_endpoint(request: Request) -> JSONResponse:
    """Return JSON state of today's schedule, intake adherence, wellness logs, alerts, and MCP log."""
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
            "mcp_logs": list(mcp_call_history),
        }
    )


async def reset_endpoint(request: Request) -> JSONResponse:
    """Clear today's logs and alerts while preserving medication schedule."""
    reset_db()
    mcp_call_history.clear()
    return JSONResponse(
        {
            "status": "success",
            "message": "Logs and alerts cleared, medication schedule preserved.",
            "mcp_logs": [],
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
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    print("=" * 65)
    print(f" CareLoop Server - Starting on http://{host}:{port}")
    print(f" - Web Dashboard    : http://localhost:{port}/")
    print(f" - MCP Endpoint     : http://localhost:{port}/mcp (Streamable HTTP)")
    print(f" - API Chat (MCP)   : http://localhost:{port}/api/chat (POST)")
    print(f" - API Dashboard    : http://localhost:{port}/api/dashboard (GET)")
    print(f" - API Reset        : http://localhost:{port}/api/reset (POST)")
    print("=" * 65)
    uvicorn.run(app, host=host, port=port)
