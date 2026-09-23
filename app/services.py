from dataclasses import dataclass

import httpx

from app.auth.identity import IdentityClient
from app.auth.token_manager import TokenManager
from app.config import Settings
from app.storage.crypto import Encryptor
from app.storage.db import Database
from app.storage.repository import Repository
from app.trimble.client import RegionDirectory, TrimbleClient


@dataclass
class Services:
    settings: Settings
    http: httpx.AsyncClient
    db: Database
    repo: Repository
    identity: IdentityClient
    tokens: TokenManager
    regions: RegionDirectory

    def trimble_for(self, user_id: str) -> TrimbleClient:
        return TrimbleClient(user_id, self.settings, self.http, self.tokens, self.regions, self.repo)

    async def aclose(self) -> None:
        await self.http.aclose()
        self.db.close()


def build_services(settings: Settings, http: httpx.AsyncClient | None = None) -> Services:
    http = http or httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    db = Database(settings.database_path)
    repo = Repository(db, Encryptor(settings.token_encryption_key))
    identity = IdentityClient(settings, http)
    return Services(
        settings=settings,
        http=http,
        db=db,
        repo=repo,
        identity=identity,
        tokens=TokenManager(repo, identity),
        regions=RegionDirectory(settings, http),
    )


_services: Services | None = None


def set_services(s: Services | None) -> None:
    global _services
    _services = s


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("Services not initialised")
    return _services
