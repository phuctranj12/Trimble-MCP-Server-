from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services import get_services
from app.trimble import normalize as n
from app.trimble.errors import ErrorCode, TrimbleError

router = APIRouter(prefix="/api")

_STATUS = {
    ErrorCode.NOT_CONNECTED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.UNSUPPORTED: 501,
    ErrorCode.NETWORK_ERROR: 502,
    ErrorCode.UPSTREAM_ERROR: 502,
}


def current_user_id(request: Request) -> str:
    uid = request.session.get("uid")
    if not uid or not get_services().repo.get_user(uid):
        raise HTTPException(401, "Not signed in")
    return uid


def _err(e: TrimbleError) -> JSONResponse:
    return JSONResponse(e.to_dict(), status_code=_STATUS.get(e.code, 502))


@router.get("/me")
async def me(request: Request):
    uid = request.session.get("uid")
    svc = get_services()
    user = svc.repo.get_user(uid) if uid else None
    if not user:
        return {"signed_in": False}
    tokens = svc.repo.get_tokens(uid)
    return {
        "signed_in": True,
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "trimble_connected": tokens is not None,
        "token_expires_at": tokens.expires_at if tokens else None,
    }


@router.get("/projects")
async def projects(uid: str = Depends(current_user_id)):
    try:
        items, region_errors = await get_services().trimble_for(uid).list_projects()
    except TrimbleError as e:
        return _err(e)
    return n.envelope({"count": len(items), "projects": [n.project_summary(p) for p in items],
                       "region_errors": region_errors})


@router.get("/projects/{project_id}")
async def project(project_id: str, uid: str = Depends(current_user_id)):
    try:
        data, region = await get_services().trimble_for(uid).get_project(project_id)
    except TrimbleError as e:
        return _err(e)
    return n.envelope({"project": n.project_summary(data)}, project_id=project_id, region=region)


class NewKey(BaseModel):
    label: str | None = Field(default=None, max_length=100)


@router.get("/mcp-keys")
async def list_keys(uid: str = Depends(current_user_id)):
    return {"keys": get_services().repo.list_mcp_keys(uid)}


@router.post("/mcp-keys")
async def create_key(body: NewKey, uid: str = Depends(current_user_id)):
    key_id, raw = get_services().repo.create_mcp_key(uid, body.label)
    return {"id": key_id, "key": raw, "note": "Shown only once. Store it in your MCP client config."}


@router.delete("/mcp-keys/{key_id}")
async def delete_key(key_id: str, uid: str = Depends(current_user_id)):
    if not get_services().repo.delete_mcp_key(uid, key_id):
        raise HTTPException(404, "Key not found")
    return {"ok": True}
