"""Read-only Trimble Connect client acting with a single user's token."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.auth.token_manager import ReauthRequired, TokenManager
from app.config import Settings
from app.storage.repository import Repository
from app.trimble.errors import ErrorCode, TrimbleError

REGIONS_TTL_SECONDS = 3600
MAX_RETRY_AFTER_SECONDS = 10

# Fallback BCF (Topics) hosts by Connect origin, used only if the regions response has no topics URL.
# Verify against the regions payload during phase 0 before relying on it.
_TOPICS_FALLBACK = {
    "app.connect.trimble.com": "https://open11.connect.trimble.com",
    "app21.connect.trimble.com": "https://open21.connect.trimble.com",
    "app31.connect.trimble.com": "https://open31.connect.trimble.com",
    "app32.connect.trimble.com": "https://open32.connect.trimble.com",
}


@dataclass(frozen=True)
class Region:
    name: str
    api_base: str               # e.g. https://app.connect.trimble.com/tc/api/2.0
    topics_base: str | None     # e.g. https://open11.connect.trimble.com


def _with_scheme(url: str) -> str:
    return url if url.startswith("http") else f"https://{url}"


def parse_regions(payload: Any) -> list[Region]:
    items = payload if isinstance(payload, list) else payload.get("items", []) if isinstance(payload, dict) else []
    regions: list[Region] = []
    for item in items:
        api = item.get("tc-api") or item.get("tcApi") or item.get("api")
        if not api:
            origin = item.get("origin")
            if not origin:
                continue
            api = f"{_with_scheme(origin).rstrip('/')}/tc/api/2.0"
        api = _with_scheme(api).rstrip("/")
        name = item.get("location") or item.get("region") or item.get("name") or api
        topics = item.get("topics-api") or item.get("bcf-api") or item.get("topicsApi")
        if topics:
            topics = _with_scheme(topics).rstrip("/")
        else:
            host = httpx.URL(api).host
            topics = _TOPICS_FALLBACK.get(host)
        regions.append(Region(name=name, api_base=api, topics_base=topics))
    return regions


class RegionDirectory:
    """Caches the public regions list shared by all users."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self._regions: list[Region] = []
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def all(self) -> list[Region]:
        async with self._lock:
            if self._regions and time.time() - self._fetched_at < REGIONS_TTL_SECONDS:
                return self._regions
            try:
                resp = await self.http.get(self.s.trimble_connect_regions_url)
            except httpx.HTTPError as e:
                raise TrimbleError(ErrorCode.NETWORK_ERROR, f"Cannot reach Trimble regions: {type(e).__name__}") from e
            if resp.status_code != 200:
                raise TrimbleError(ErrorCode.UPSTREAM_ERROR, "Regions endpoint failed", resp.status_code)
            regions = parse_regions(resp.json())
            if not regions:
                raise TrimbleError(ErrorCode.UPSTREAM_ERROR, "Regions endpoint returned no usable regions")
            self._regions, self._fetched_at = regions, time.time()
            return regions

    async def get(self, name: str) -> Region:
        for r in await self.all():
            if r.name == name:
                return r
        raise TrimbleError(ErrorCode.NOT_FOUND, f"Unknown region '{name}'")


@dataclass
class Page:
    items: list[dict]
    truncated: bool
    next_url: str | None


class TrimbleClient:
    def __init__(
        self,
        user_id: str,
        settings: Settings,
        http: httpx.AsyncClient,
        tokens: TokenManager,
        regions: RegionDirectory,
        repo: Repository,
    ):
        self.user_id = user_id
        self.s = settings
        self.http = http
        self.tokens = tokens
        self.regions = regions
        self.repo = repo

    # ---------- low level ----------
    async def _token(self, force: bool = False) -> str:
        try:
            return await self.tokens.get_access_token(self.user_id, force_refresh=force)
        except ReauthRequired as e:
            raise TrimbleError(ErrorCode.NOT_CONNECTED, str(e)) from e

    async def request_json(self, url: str, params: dict | None = None) -> tuple[Any, httpx.Response]:
        refreshed = False
        attempt = 0
        while True:
            token = await self._token(force=refreshed)
            try:
                resp = await self.http.get(
                    url, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
                )
            except httpx.TimeoutException as e:
                if attempt < self.s.http_max_retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                raise TrimbleError(ErrorCode.NETWORK_ERROR, "Trimble request timed out") from e
            except httpx.HTTPError as e:
                if attempt < self.s.http_max_retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * 2**attempt)
                    continue
                raise TrimbleError(ErrorCode.NETWORK_ERROR, f"Network error: {type(e).__name__}") from e

            status = resp.status_code
            if status == 401 and not refreshed:
                refreshed = True
                continue
            if status in (429, 502, 503, 504) and attempt < self.s.http_max_retries:
                attempt += 1
                await asyncio.sleep(_retry_delay(resp, attempt))
                continue
            if 200 <= status < 300:
                return (resp.json() if resp.content else None), resp
            raise _error_for(resp)

    async def get_paged(self, url: str, params: dict | None = None, max_pages: int | None = None) -> Page:
        max_pages = max_pages or self.s.max_pages
        items: list[dict] = []
        next_url: str | None = url
        next_params = params
        pages = 0
        while next_url and pages < max_pages:
            data, resp = await self.request_json(next_url, next_params)
            pages += 1
            if isinstance(data, list):
                items.extend(data)
            elif isinstance(data, dict):
                items.extend(data.get("items") or data.get("data") or [])
            nxt = resp.links.get("next", {}).get("url")
            next_url, next_params = (str(resp.url.join(nxt)), None) if nxt else (None, None)
        return Page(items=items, truncated=next_url is not None, next_url=next_url)

    # ---------- projects ----------
    async def list_projects(self) -> tuple[list[dict], list[dict]]:
        """Return (projects, region_errors) across all regions, remembering project -> region."""
        regions = await self.regions.all()
        results = await asyncio.gather(
            *(self.get_paged(f"{r.api_base}/projects", {"fullyLoaded": "true"}) for r in regions),
            return_exceptions=True,
        )
        projects: list[dict] = []
        errors: list[dict] = []
        mapping: dict[str, str] = {}
        for region, res in zip(regions, results):
            if isinstance(res, TrimbleError):
                if res.code == ErrorCode.NOT_CONNECTED:
                    raise res
                errors.append({"region": region.name, **res.to_dict()["error"]})
                continue
            if isinstance(res, BaseException):
                raise res
            for p in res.items:
                pid = p.get("id")
                if not pid:
                    continue
                mapping[pid] = region.name
                projects.append({**p, "_region": region.name})
            if res.truncated:
                errors.append({"region": region.name, "code": "truncated", "message": "More pages available"})
        if mapping:
            self.repo.save_project_regions(self.user_id, mapping)
        return projects, errors

    async def region_for_project(self, project_id: str) -> Region:
        name = self.repo.get_project_region(self.user_id, project_id)
        if name is None:
            await self.list_projects()
            name = self.repo.get_project_region(self.user_id, project_id)
        if name is None:
            raise TrimbleError(
                ErrorCode.NOT_FOUND, "Project not found or not accessible with your Trimble account"
            )
        return await self.regions.get(name)

    async def get_project(self, project_id: str) -> tuple[dict, Region]:
        region = await self.region_for_project(project_id)
        data, _ = await self.request_json(f"{region.api_base}/projects/{project_id}", {"fullyLoaded": "true"})
        return data, region

    async def list_folder_items(self, project_id: str, folder_id: str | None) -> tuple[Page, str, Region]:
        project, region = await self.get_project(project_id)
        folder = folder_id or project.get("rootId")
        if not folder:
            raise TrimbleError(ErrorCode.UNSUPPORTED, "Project has no root folder id")
        page = await self.get_paged(f"{region.api_base}/folders/{folder}/items")
        return page, folder, region

    async def list_topics(self, project_id: str) -> tuple[Page, Region]:
        region = await self.region_for_project(project_id)
        if not region.topics_base:
            raise TrimbleError(ErrorCode.UNSUPPORTED, f"No Topics (BCF) endpoint known for region {region.name}")
        page = await self.get_paged(f"{region.topics_base}/bcf/2.1/projects/{project_id}/topics")
        return page, region


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    ra = resp.headers.get("Retry-After")
    if ra and ra.isdigit():
        return min(float(ra), MAX_RETRY_AFTER_SECONDS)
    return min(0.5 * 2**attempt, MAX_RETRY_AFTER_SECONDS)


def _error_for(resp: httpx.Response) -> TrimbleError:
    s = resp.status_code
    if s == 401:
        return TrimbleError(ErrorCode.NOT_CONNECTED, "Trimble rejected the token; reconnect your account", s)
    if s == 403:
        return TrimbleError(ErrorCode.FORBIDDEN, "Your Trimble account has no access to this resource", s)
    if s == 404:
        return TrimbleError(ErrorCode.NOT_FOUND, "Not found or not accessible with your Trimble account", s)
    if s == 429:
        return TrimbleError(ErrorCode.RATE_LIMITED, "Trimble rate limit reached; try again later", s)
    return TrimbleError(ErrorCode.UPSTREAM_ERROR, f"Trimble API error {s}", s)
