from enum import Enum


class ErrorCode(str, Enum):
    NOT_CONNECTED = "not_connected"          # user must (re)connect Trimble
    FORBIDDEN = "forbidden"                  # Trimble denied access with this user's token
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    NETWORK_ERROR = "network_error"
    UNSUPPORTED = "unsupported"              # e.g. endpoint not available for this region


class TrimbleError(Exception):
    def __init__(self, code: ErrorCode, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict:
        return {"error": {"code": self.code.value, "message": self.message, "http_status": self.status}}
