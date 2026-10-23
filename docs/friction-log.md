# CareLoop Friction Log

Developer friction encountered while building and integrating CareLoop.

| Task | Steps | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|
| FastMCP SDK import (SDK v2) | Imported FastMCP from mcp.server.fastmcp under mcp>=2.0.0 | Clean import of the server class | ModuleNotFoundError: FastMCP was renamed to MCPServer in mcp 2.x | Medium | Imported MCPServer from mcp.server.mcpserver and aliased it to FastMCP | Add a backwards-compatible alias or document the migration in quickstarts |
| Streamable HTTP mounted in Starlette | Mounted the MCP app at /mcp with Mount | POST to /mcp without a trailing slash runs directly | Starlette returns a 307 redirect to /mcp/, which breaks some non-browser POST clients | Medium | Added both a direct Route for /mcp and a Mount for /mcp | Normalize paths without redirects when mounting sub-apps |
| DNS rebinding and origin protection | Connected MCP Inspector to http://localhost:8000/mcp | Inspector connects and lists tools | Request rejected with 421 Misdirected Request from default host validation | Low | Disabled DNS rebinding protection for local development only | Allow the standard Inspector origin by default in dev mode |
| Intent keyword variation | Tapped the chip "Did I miss anything?" | Matches the check_missed_meds intent | Pattern only matched "missed", so "miss" fell through to the fallback reply | Low | Expanded the intent pattern to also match "miss" and "missing" | Use stem-based matching for rule-based intents |
| Port conflict on 8000 | Ran python server.py while another service used port 8000 | Server starts or reports the conflict clearly | OSError: WinError 10048, address already in use | Low | Added PORT environment variable support | Keep 8000 as default but allow an override |
| Start the Antigravity CLI | Ran the install script, then pasted a long prompt into the terminal | Prompt goes to the agent | PowerShell treated it as commands; agy was not on PATH until a new terminal | Medium | Opened a new terminal and ran agy from the project folder | Print a clear next-step message after install |
| Long agent run on free plan | Ran the build prompt, then more prompts | Edits complete | "Agent terminated due to error", later "Individual quota reached" | High | Started new conversations and sent fewer, larger prompts | Show the cause and remaining quota before a long run |
| Open the local server | Opened the address shown in the server log | Page loads | 0.0.0.0 gives ERR_ADDRESS_INVALID in the browser | Low | Opened localhost:8000 instead | Log http://localhost:PORT |
| Add a server in MCP Inspector | Clicked Add server | Obvious way to add an HTTP server | The form defaults to stdio and asks for a command | Low | Switched transport to Streamable HTTP and entered the URL | Remember the last used transport or add a hint |
