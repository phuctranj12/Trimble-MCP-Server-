"""MCP tools. The caller is identified by the MCP access key in the Authorization header,
never by a tool argument, and every Trimble call uses that caller's own token."""

import json
from collections import Counter

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services import get_services
from app.trimble import normalize as n
from app.trimble.client import TrimbleClient
from app.trimble.errors import ErrorCode, TrimbleError

INSTRUCTIONS = (
    "Read-only access to Trimble Connect projects with the caller's own Trimble permissions. "
    "Every result carries source, fetched_at and project_id; cite them. "
    "These tools do not provide schedule/progress data: never conclude a project is late or on track "
    "from files or topics alone. If a tool returns not_connected, tell the user to reconnect Trimble "
    "in the web portal."
)

mcp = MCPServer("trimble-connect", instructions=INSTRUCTIONS)

USER_STATE_KEY = "mcp_user_id"


class MCPAuthMiddleware:
    """Resolves the MCP access key to a user before the MCP app sees the request."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        user_id = None
        if auth.lower().startswith("bearer "):
            user_id = get_services().repo.resolve_mcp_key(auth[7:].strip())
        if not user_id:
            body = json.dumps({"error": "invalid_token", "message": "Missing or invalid MCP access key"}).encode()
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
            })
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})[USER_STATE_KEY] = user_id
        await self.app(scope, receive, send)


def _client(ctx: Context) -> TrimbleClient:
    request = ctx.request_context.request
    user_id = getattr(request.state, USER_STATE_KEY, None) if request is not None else None
    if not user_id:
        raise ToolError(json.dumps({"error": {"code": "unauthenticated", "message": "Caller not identified"}}))
    return get_services().trimble_for(user_id)


def _fail(e: TrimbleError) -> ToolError:
    return ToolError(json.dumps(e.to_dict(), ensure_ascii=False))


@mcp.tool()
async def list_projects(ctx: Context) -> dict:
    """List Trimble Connect projects the calling user can access, across all regions."""
    client = _client(ctx)
    try:
        projects, region_errors = await client.list_projects()
    except TrimbleError as e:
        raise _fail(e)
    return n.envelope({
        "count": len(projects),
        "projects": [n.project_summary(p) for p in projects],
        "region_errors": region_errors,
    })


@mcp.tool()
async def get_project(project_id: str, ctx: Context) -> dict:
    """Get details of one Trimble Connect project (must be accessible to the calling user)."""
    client = _client(ctx)
    try:
        project, region = await client.get_project(project_id)
    except TrimbleError as e:
        raise _fail(e)
    return n.envelope({"project": n.project_summary(project)}, project_id=project_id, region=region)


@mcp.tool()
async def list_project_files(project_id: str, ctx: Context, folder_id: str | None = None) -> dict:
    """List files and folders in a project folder (root folder when folder_id is omitted)."""
    client = _client(ctx)
    try:
        page, folder, region = await client.list_folder_items(project_id, folder_id)
    except TrimbleError as e:
        raise _fail(e)
    return n.envelope({
        "folder_id": folder,
        "count": len(page.items),
        "items": [n.folder_item(i) for i in page.items],
        "truncated": page.truncated,
        "next_page": page.next_url,
    }, project_id=project_id, region=region)


@mcp.tool()
async def list_open_topics(project_id: str, ctx: Context) -> dict:
    """List BCF topics (issues) of a project that are not in a closed status."""
    client = _client(ctx)
    try:
        page, region = await client.list_topics(project_id)
    except TrimbleError as e:
        raise _fail(e)
    open_topics = [t for t in page.items if n.is_open_topic(t)]
    return n.envelope({
        "total_topics": len(page.items),
        "open_count": len(open_topics),
        "status_counts": dict(Counter(str(t.get("topic_status") or "unknown") for t in page.items)),
        "closed_statuses_assumed": sorted(n.CLOSED_TOPIC_STATUSES),
        "topics": [n.topic(t) for t in open_topics],
        "truncated": page.truncated,
    }, project_id=project_id, region=region)


@mcp.tool()
async def project_overview(project_id: str, ctx: Context) -> dict:
    """Summarise verified facts about a project: details, root folder contents, and topic counts.
    Does not assess schedule or progress."""
    client = _client(ctx)
    try:
        project, region = await client.get_project(project_id)
    except TrimbleError as e:
        raise _fail(e)

    sections: dict = {"project": n.project_summary(project)}
    unavailable: dict = {}

    try:
        page, folder, _ = await client.list_folder_items(project_id, None)
        kinds = Counter(str(i.get("type") or "unknown") for i in page.items)
        sections["root_folder"] = {"folder_id": folder, "item_count": len(page.items),
                                   "by_type": dict(kinds), "truncated": page.truncated}
    except TrimbleError as e:
        if e.code == ErrorCode.NOT_CONNECTED:
            raise _fail(e)
        unavailable["root_folder"] = e.to_dict()["error"]

    try:
        tpage, _ = await client.list_topics(project_id)
        sections["topics"] = {
            "total": len(tpage.items),
            "open": sum(1 for t in tpage.items if n.is_open_topic(t)),
            "status_counts": dict(Counter(str(t.get("topic_status") or "unknown") for t in tpage.items)),
            "truncated": tpage.truncated,
        }
    except TrimbleError as e:
        if e.code == ErrorCode.NOT_CONNECTED:
            raise _fail(e)
        unavailable["topics"] = e.to_dict()["error"]

    sections["unavailable"] = unavailable
    sections["limitations"] = "No schedule data connected; progress/delay cannot be determined from this overview."
    return n.envelope(sections, project_id=project_id, region=region)
