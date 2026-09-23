import asyncio
import time
from collections import defaultdict

from app.auth.identity import IdentityClient, IdentityError
from app.storage.repository import Repository, StoredTokens

EXPIRY_SKEW_SECONDS = 60


class ReauthRequired(Exception):
    """The user must sign in to Trimble again (no token, or refresh failed)."""


class TokenManager:
    def __init__(self, repo: Repository, identity: IdentityClient):
        self.repo = repo
        self.identity = identity
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_access_token(self, user_id: str, force_refresh: bool = False) -> str:
        tokens = self.repo.get_tokens(user_id)
        if not tokens:
            raise ReauthRequired("Not connected to Trimble")
        if not force_refresh and tokens.expires_at - EXPIRY_SKEW_SECONDS > time.time():
            return tokens.access_token

        # Refresh tokens are single-use: serialize refreshes per user.
        async with self._locks[user_id]:
            current = self.repo.get_tokens(user_id)
            if not current:
                raise ReauthRequired("Not connected to Trimble")
            if current.access_token != tokens.access_token:
                return current.access_token
            if not current.refresh_token or not current.next_verifier:
                raise ReauthRequired("Trimble session expired")
            try:
                result = await self.identity.refresh(current.refresh_token, current.next_verifier)
            except IdentityError as e:
                if e.status is not None and 400 <= e.status < 500:
                    self.repo.delete_tokens(user_id)
                    raise ReauthRequired("Trimble refresh rejected; sign in again") from e
                raise
            self.repo.save_tokens(
                user_id,
                StoredTokens(
                    access_token=result.access_token,
                    refresh_token=result.refresh_token or current.refresh_token,
                    next_verifier=result.next_verifier,
                    expires_at=result.expires_at,
                ),
            )
            return result.access_token
