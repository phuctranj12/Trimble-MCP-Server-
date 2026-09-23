import json
import time
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.services import build_services, set_services
from app.storage.repository import StoredTokens

API = "https://app.connect.trimble.com/tc/api/2.0"
REGIONS_URL = f"{API}/regions"
ID_BASE = "https://id.example.test"

# token -> user; each user sees only their own projects
TOKENS = {"tok-a": "user-a", "tok-a2": "user-a", "tok-b": "user-b"}
PROJECTS = {
    "user-a": [{"id": "pA1", "name": "Project A1", "rootId": "fA1"},
               {"id": "pA2", "name": "Project A2", "rootId": "fA2"}],
    "user-b": [{"id": "pB1", "name": "Project B1", "rootId": "fB1"}],
}


class FakeTrimble:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.token_responses: list[httpx.Response] = []
        self.fail_next_401 = False

    def user(self, req: httpx.Request) -> str | None:
        auth = req.headers.get("authorization", "")
        return TOKENS.get(auth.removeprefix("Bearer "))

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        url = str(req.url)
        if url == REGIONS_URL:
            return httpx.Response(200, json=[{"location": "northAmerica", "tc-api": API + "/"}])
        if url.startswith(ID_BASE + "/oauth/token"):
            return self.token_responses.pop(0)
        if url.startswith(ID_BASE + "/oauth/revoke"):
            return httpx.Response(200)

        uid = self.user(req)
        if self.fail_next_401:
            self.fail_next_401 = False
            return httpx.Response(401)
        if uid is None:
            return httpx.Response(401)
        path = req.url.path.removeprefix("/tc/api/2.0")
        mine = {p["id"]: p for p in PROJECTS[uid]}

        if path == "/projects":
            # two pages for user A to exercise Link pagination
            if uid == "user-a" and req.url.params.get("page") != "2":
                return httpx.Response(200, json=[PROJECTS[uid][0]],
                                      headers={"Link": f'<{API}/projects?page=2>; rel="next"'})
            if uid == "user-a":
                return httpx.Response(200, json=[PROJECTS[uid][1]])
            return httpx.Response(200, json=PROJECTS[uid])
        if path.startswith("/projects/"):
            pid = path.split("/")[2]
            return httpx.Response(200, json=mine[pid]) if pid in mine else httpx.Response(403)
        if path.startswith("/folders/"):
            return httpx.Response(200, json=[{"id": "file1", "name": "Model.ifc", "type": "FILE"},
                                             {"id": "dir1", "name": "Docs", "type": "FOLDER"}])
        if req.url.host == "open11.connect.trimble.com":
            pid = req.url.path.split("/")[4]
            if pid not in mine:
                return httpx.Response(403)
            return httpx.Response(200, json=[
                {"guid": "t1", "title": "Clash", "topic_status": "Open"},
                {"guid": "t2", "title": "Done", "topic_status": "Closed"},
            ])
        return httpx.Response(404)


def token_response(access: str, refresh: str = "r-new", expires_in: int = 3600, id_claims: dict | None = None):
    body = {"access_token": access, "refresh_token": refresh, "expires_in": expires_in}
    if id_claims:
        import base64
        payload = base64.urlsafe_b64encode(json.dumps(id_claims).encode()).rstrip(b"=").decode()
        body["id_token"] = f"h.{payload}.s"
    return httpx.Response(200, json=body)


def form(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        trimble_client_id="client-123",
        trimble_scope="openid testapp",
        trimble_redirect_uri="http://localhost:8000/auth/trimble/callback",
        trimble_identity_base=ID_BASE,
        trimble_connect_regions_url=REGIONS_URL,
        token_encryption_key=Fernet.generate_key().decode(),
        session_secret="test-secret",
        database_path=str(tmp_path / "test.db"),
        http_max_retries=1,
    )


@pytest.fixture
def fake() -> FakeTrimble:
    return FakeTrimble()


@pytest.fixture
async def services(settings, fake):
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    svc = build_services(settings, http)
    set_services(svc)
    yield svc
    set_services(None)
    await svc.aclose()


def connect(svc, user_id: str, access: str, expires_in: float = 3600):
    svc.repo.upsert_user(user_id, f"{user_id}@example.test", user_id)
    svc.repo.save_tokens(user_id, StoredTokens(access, f"refresh-{user_id}", f"verifier-{user_id}",
                                               time.time() + expires_in))
