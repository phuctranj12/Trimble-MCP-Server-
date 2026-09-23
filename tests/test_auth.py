import pytest

from app.auth.pkce import challenge_for
from app.auth.token_manager import ReauthRequired
from tests.conftest import connect, form, token_response


def test_pkce_challenge_format():
    from app.auth.pkce import new_verifier
    v = new_verifier()
    c = challenge_for(v)
    assert 43 <= len(v) <= 128
    assert len(c) == 43 and "=" not in c and "+" not in c and "/" not in c
    assert challenge_for(v) == c


def test_tokens_encrypted_at_rest(services):
    connect(services, "user-a", "tok-a")
    row = services.db.fetchone("SELECT access_token_enc FROM trimble_tokens WHERE user_id='user-a'")
    assert b"tok-a" not in row["access_token_enc"]


async def test_serial_pkce_refresh(services, fake):
    connect(services, "user-a", "tok-old", expires_in=-10)
    fake.token_responses.append(token_response("tok-a2", refresh="refresh-2"))

    token = await services.tokens.get_access_token("user-a")

    assert token == "tok-a2"
    sent = form(fake.calls[-1])
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "refresh-user-a"
    assert sent["code_verifier"] == "verifier-user-a"
    assert sent["code_challenge_method"] == "S256"
    stored = services.repo.get_tokens("user-a")
    assert stored.refresh_token == "refresh-2"
    assert challenge_for(stored.next_verifier) == sent["code_challenge"]


async def test_refresh_rejected_requires_reauth(services, fake):
    import httpx
    connect(services, "user-a", "tok-old", expires_in=-10)
    fake.token_responses.append(httpx.Response(400, json={"error": "invalid_grant"}))

    with pytest.raises(ReauthRequired):
        await services.tokens.get_access_token("user-a")
    assert services.repo.get_tokens("user-a") is None


def test_pending_state_single_use(services):
    services.repo.save_pending("s1", "v1")
    assert services.repo.pop_pending("s1") == "v1"
    assert services.repo.pop_pending("s1") is None


def test_mcp_key_hashed_and_resolves(services):
    connect(services, "user-a", "tok-a")
    key_id, raw = services.repo.create_mcp_key("user-a", "desktop")
    assert services.repo.resolve_mcp_key(raw) == "user-a"
    assert services.repo.resolve_mcp_key(raw + "x") is None
    row = services.db.fetchone("SELECT key_hash FROM mcp_keys WHERE id=?", (key_id,))
    assert raw not in row["key_hash"]
    assert services.repo.delete_mcp_key("user-b", key_id) is False
    assert services.repo.delete_mcp_key("user-a", key_id) is True
    assert services.repo.resolve_mcp_key(raw) is None
