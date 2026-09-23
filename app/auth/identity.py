"""Trimble Identity OAuth client (Authorization Code + PKCE, Serial PKCE refresh)."""

import base64
import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.auth.pkce import challenge_for, new_verifier
from app.config import Settings


class IdentityError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class TokenResult:
    access_token: str
    refresh_token: str | None
    expires_at: float
    next_verifier: str
    id_claims: dict


def _decode_jwt_claims(token: str | None) -> dict:
    # id_token is received directly from the token endpoint over TLS, so claims are
    # read without signature verification (OIDC Core 3.1.3.7).
    if not token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


class IdentityClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        base = settings.trimble_identity_base.rstrip("/")
        self.authorize_url = f"{base}/oauth/authorize"
        self.token_url = f"{base}/oauth/token"
        self.revoke_url = f"{base}/oauth/revoke"

    def build_authorize_url(self, state: str, verifier: str) -> str:
        params = {
            "client_id": self.s.trimble_client_id,
            "response_type": "code",
            "redirect_uri": self.s.trimble_redirect_uri,
            "scope": self.s.trimble_scope,
            "state": state,
            "code_challenge": challenge_for(verifier),
            "code_challenge_method": "S256",
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    def _auth(self) -> tuple[str, str] | None:
        if self.s.trimble_client_secret:
            return (self.s.trimble_client_id, self.s.trimble_client_secret)
        return None

    async def _token_request(self, data: dict) -> TokenResult:
        next_verifier = new_verifier()
        data = {
            **data,
            "client_id": self.s.trimble_client_id,
            "code_challenge": challenge_for(next_verifier),
            "code_challenge_method": "S256",
        }
        try:
            resp = await self.http.post(self.token_url, data=data, auth=self._auth())
        except httpx.HTTPError as e:
            raise IdentityError(f"Cannot reach Trimble Identity: {type(e).__name__}") from e
        if resp.status_code != 200:
            err = _safe_error(resp)
            raise IdentityError(f"Token endpoint returned {resp.status_code}: {err}", resp.status_code)
        body = resp.json()
        return TokenResult(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=time.time() + int(body.get("expires_in", 3600)),
            next_verifier=next_verifier,
            id_claims=_decode_jwt_claims(body.get("id_token")),
        )

    async def exchange_code(self, code: str, verifier: str) -> TokenResult:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.s.trimble_redirect_uri,
                "code_verifier": verifier,
            }
        )

    async def refresh(self, refresh_token: str, verifier: str) -> TokenResult:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token, "code_verifier": verifier}
        )

    async def revoke(self, token: str) -> None:
        try:
            await self.http.post(
                self.revoke_url,
                data={"token": token, "client_id": self.s.trimble_client_id},
                auth=self._auth(),
            )
        except httpx.HTTPError:
            pass


def _safe_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error_description") or body.get("error") or "unknown_error")
    except ValueError:
        return "non-json response"
