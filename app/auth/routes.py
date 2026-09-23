import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth.identity import IdentityError
from app.auth.pkce import new_state, new_verifier
from app.services import get_services
from app.storage.repository import StoredTokens

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")


def _front(path: str = "/") -> str:
    return get_services().settings.frontend_url.rstrip("/") + path


@router.get("/trimble/start")
async def start(request: Request):
    svc = get_services()
    if not svc.settings.trimble_client_id:
        raise HTTPException(500, "TRIMBLE_CLIENT_ID is not configured")
    state, verifier = new_state(), new_verifier()
    svc.repo.save_pending(state, verifier)
    request.session["oauth_state"] = state
    return RedirectResponse(svc.identity.build_authorize_url(state, verifier), status_code=302)


@router.get("/trimble/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None, error_description: str | None = None):
    svc = get_services()
    expected = request.session.pop("oauth_state", None)
    if error:
        log.warning("Trimble login returned error=%s", error)
        return RedirectResponse(_front(f"/?login_error={error}"), status_code=302)
    if not code or not state or not expected or state != expected:
        return RedirectResponse(_front("/?login_error=invalid_state"), status_code=302)
    verifier = svc.repo.pop_pending(state)
    if not verifier:
        return RedirectResponse(_front("/?login_error=expired_state"), status_code=302)

    try:
        result = await svc.identity.exchange_code(code, verifier)
    except IdentityError as e:
        log.warning("Token exchange failed: %s", e)
        return RedirectResponse(_front("/?login_error=token_exchange_failed"), status_code=302)

    claims = result.id_claims
    user_id = claims.get("sub")
    if not user_id:
        return RedirectResponse(_front("/?login_error=no_identity"), status_code=302)
    name = claims.get("name") or " ".join(filter(None, [claims.get("given_name"), claims.get("family_name")])) or None
    svc.repo.upsert_user(user_id, claims.get("email"), name)
    svc.repo.save_tokens(user_id, StoredTokens(result.access_token, result.refresh_token,
                                               result.next_verifier, result.expires_at))
    request.session.clear()
    request.session["uid"] = user_id
    log.info("User %s connected Trimble", user_id)
    return RedirectResponse(_front("/"), status_code=302)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.post("/trimble/disconnect")
async def disconnect(request: Request):
    svc = get_services()
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Not signed in")
    tokens = svc.repo.get_tokens(uid)
    if tokens and tokens.refresh_token:
        await svc.identity.revoke(tokens.refresh_token)
    svc.repo.delete_tokens(uid)
    return {"ok": True}
