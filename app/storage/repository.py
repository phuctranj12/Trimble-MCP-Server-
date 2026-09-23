import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass

from app.storage.crypto import Encryptor
from app.storage.db import Database

PENDING_AUTH_TTL_SECONDS = 600
MCP_KEY_PREFIX = "tmcp_"


@dataclass
class StoredTokens:
    access_token: str
    refresh_token: str | None
    next_verifier: str | None
    expires_at: float


@dataclass
class User:
    id: str
    email: str | None
    name: str | None


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class Repository:
    def __init__(self, db: Database, enc: Encryptor):
        self.db = db
        self.enc = enc

    # --- users ---
    def upsert_user(self, user_id: str, email: str | None, name: str | None) -> User:
        now = time.time()
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO users (id, email, name, created_at, updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET email=excluded.email, name=excluded.name,
                   updated_at=excluded.updated_at""",
                (user_id, email, name, now, now),
            )
        return User(user_id, email, name)

    def get_user(self, user_id: str) -> User | None:
        row = self.db.fetchone("SELECT id, email, name FROM users WHERE id=?", (user_id,))
        return User(row["id"], row["email"], row["name"]) if row else None

    # --- pending OAuth (state -> PKCE verifier) ---
    def save_pending(self, state: str, verifier: str) -> None:
        now = time.time()
        with self.db.tx() as c:
            c.execute("DELETE FROM oauth_pending WHERE created_at < ?", (now - PENDING_AUTH_TTL_SECONDS,))
            c.execute(
                "INSERT INTO oauth_pending (state, verifier_enc, created_at) VALUES (?,?,?)",
                (state, self.enc.encrypt(verifier), now),
            )

    def pop_pending(self, state: str) -> str | None:
        with self.db.tx() as c:
            row = c.execute(
                "SELECT verifier_enc, created_at FROM oauth_pending WHERE state=?", (state,)
            ).fetchone()
            c.execute("DELETE FROM oauth_pending WHERE state=?", (state,))
        if not row or row["created_at"] < time.time() - PENDING_AUTH_TTL_SECONDS:
            return None
        return self.enc.decrypt(row["verifier_enc"])

    # --- Trimble tokens ---
    def save_tokens(self, user_id: str, tokens: StoredTokens) -> None:
        enc = self.enc.encrypt
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO trimble_tokens
                   (user_id, access_token_enc, refresh_token_enc, next_verifier_enc, expires_at, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET access_token_enc=excluded.access_token_enc,
                   refresh_token_enc=excluded.refresh_token_enc, next_verifier_enc=excluded.next_verifier_enc,
                   expires_at=excluded.expires_at, updated_at=excluded.updated_at""",
                (
                    user_id,
                    enc(tokens.access_token),
                    enc(tokens.refresh_token) if tokens.refresh_token else None,
                    enc(tokens.next_verifier) if tokens.next_verifier else None,
                    tokens.expires_at,
                    time.time(),
                ),
            )

    def get_tokens(self, user_id: str) -> StoredTokens | None:
        row = self.db.fetchone("SELECT * FROM trimble_tokens WHERE user_id=?", (user_id,))
        if not row:
            return None
        dec = self.enc.decrypt
        return StoredTokens(
            access_token=dec(row["access_token_enc"]),
            refresh_token=dec(row["refresh_token_enc"]) if row["refresh_token_enc"] else None,
            next_verifier=dec(row["next_verifier_enc"]) if row["next_verifier_enc"] else None,
            expires_at=row["expires_at"],
        )

    def delete_tokens(self, user_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM trimble_tokens WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM project_regions WHERE user_id=?", (user_id,))

    # --- MCP access keys ---
    def create_mcp_key(self, user_id: str, label: str | None) -> tuple[str, str]:
        raw = MCP_KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = uuid.uuid4().hex
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO mcp_keys (id, user_id, key_hash, label, created_at) VALUES (?,?,?,?,?)",
                (key_id, user_id, _hash_key(raw), label, time.time()),
            )
        return key_id, raw

    def list_mcp_keys(self, user_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT id, label, created_at, last_used_at FROM mcp_keys WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(r) for r in rows]

    def delete_mcp_key(self, user_id: str, key_id: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute("DELETE FROM mcp_keys WHERE id=? AND user_id=?", (key_id, user_id))
        return cur.rowcount > 0

    def resolve_mcp_key(self, raw: str) -> str | None:
        if not raw.startswith(MCP_KEY_PREFIX):
            return None
        row = self.db.fetchone("SELECT id, user_id FROM mcp_keys WHERE key_hash=?", (_hash_key(raw),))
        if not row:
            return None
        with self.db.tx() as c:
            c.execute("UPDATE mcp_keys SET last_used_at=? WHERE id=?", (time.time(), row["id"]))
        return row["user_id"]

    # --- project -> region mapping (per user, learned from listing) ---
    def save_project_regions(self, user_id: str, mapping: dict[str, str]) -> None:
        now = time.time()
        with self.db.tx() as c:
            c.executemany(
                """INSERT INTO project_regions (user_id, project_id, region, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(user_id, project_id) DO UPDATE SET region=excluded.region,
                   updated_at=excluded.updated_at""",
                [(user_id, pid, region, now) for pid, region in mapping.items()],
            )

    def get_project_region(self, user_id: str, project_id: str) -> str | None:
        row = self.db.fetchone(
            "SELECT region FROM project_regions WHERE user_id=? AND project_id=?", (user_id, project_id)
        )
        return row["region"] if row else None
