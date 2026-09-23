import base64
import hashlib
import secrets


def new_verifier() -> str:
    # 64 url-safe chars, within the 43-128 range required by RFC 7636
    return secrets.token_urlsafe(48)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(32)
