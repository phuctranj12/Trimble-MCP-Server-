import json
from contextlib import asynccontextmanager

import httpx
import pytest

from app.main import create_app
from tests.conftest import connect, form, token_response

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@asynccontextmanager
async def make_client(settings, services):
    app = create_app(settings, services)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost:8000") as c:
            yield c


def _tool_data(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _rpc_result(resp: httpx.Response) -> dict:
    text = resp.text
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


async def _rpc(client, key: str | None, method: str, params: dict | None = None) -> httpx.Response:
    headers = dict(MCP_HEADERS)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return await client.post("/mcp/", headers=headers, json=body)


async def test_mcp_requires_key(settings, services):
    async with make_client(settings, services) as client:
        resp = await _rpc(client, None, "tools/list")
        assert resp.status_code == 401
        resp = await _rpc(client, "tmcp_wrong", "tools/list")
        assert resp.status_code == 401


async def test_mcp_tools_use_callers_token(settings, services):
    async with make_client(settings, services) as client:
        connect(services, "user-a", "tok-a")
        connect(services, "user-b", "tok-b")
        _, key_a = services.repo.create_mcp_key("user-a", None)
        _, key_b = services.repo.create_mcp_key("user-b", None)

        resp = await _rpc(client, key_a, "tools/list")
        assert resp.status_code == 200, resp.text
        names = {t["name"] for t in _rpc_result(resp)["result"]["tools"]}
        assert {"list_projects", "get_project", "list_project_files", "list_open_topics",
                "project_overview"} <= names

        resp = await _rpc(client, key_a, "tools/call", {"name": "list_projects", "arguments": {}})
        result = _rpc_result(resp)["result"]
        data = _tool_data(result)
        assert [p["id"] for p in data["projects"]] == ["pA1", "pA2"]
        assert data["source"] == "trimble_connect" and "fetched_at" in data

        resp = await _rpc(client, key_b, "tools/call", {"name": "get_project", "arguments": {"project_id": "pA1"}})
        result = _rpc_result(resp)["result"]
        assert result["isError"] is True
        assert "not_found" in result["content"][0]["text"]

        resp = await _rpc(client, key_a, "tools/call",
                          {"name": "project_overview", "arguments": {"project_id": "pA1"}})
        ov = _tool_data(_rpc_result(resp)["result"])
        assert ov["topics"]["open"] == 1 and ov["root_folder"]["item_count"] == 2
        assert "tok-a" not in resp.text


async def test_oauth_start_and_callback(settings, services, fake):
    async with make_client(settings, services) as client:
        resp = await client.get("/auth/trimble/start")
        assert resp.status_code == 302
        url = httpx.URL(resp.headers["location"])
        assert url.params["code_challenge_method"] == "S256"
        state = url.params["state"]

        fake.token_responses.append(token_response("tok-a", id_claims={"sub": "user-a", "email": "a@x.test"}))
        resp = await client.get("/auth/trimble/callback", params={"code": "abc", "state": state})
        assert resp.status_code == 302 and "login_error" not in resp.headers["location"]
        sent = form(fake.calls[-1])
        assert sent["grant_type"] == "authorization_code" and sent["code_verifier"]

        me = (await client.get("/api/me")).json()
        assert me["signed_in"] and me["user"]["id"] == "user-a" and me["trimble_connected"]
        assert "tok-a" not in json.dumps(me)

        projects = (await client.get("/api/projects")).json()
        assert projects["count"] == 2


async def test_oauth_callback_rejects_bad_state(settings, services):
    async with make_client(settings, services) as client:
        await client.get("/auth/trimble/start")
        resp = await client.get("/auth/trimble/callback", params={"code": "abc", "state": "forged"})
        assert "login_error=invalid_state" in resp.headers["location"]
