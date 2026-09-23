import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import router as api_router
from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.services import Services, build_services, set_services
from app.tools.server import MCPAuthMiddleware, mcp


class _RedactAuthFilter(logging.Filter):
    """Keep bearer tokens and OAuth codes out of logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "Bearer " in msg or "code=" in msg or "refresh_token" in msg:
            record.msg, record.args = "[redacted log line]", ()
        return True


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set")

    local_hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=local_hosts + settings.mcp_allowed_hosts,
            allowed_origins=[f"http://{h}" for h in local_hosts] + [f"https://{h}" for h in settings.mcp_allowed_hosts],
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        svc = services or build_services(settings)
        set_services(svc)
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await svc.aclose()
            set_services(None)

    app = FastAPI(title="Trimble Connect MCP", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="tmcp_session",
        same_site="lax",
        https_only=settings.cookie_secure,
        max_age=8 * 3600,
    )
    app.include_router(auth_router)
    app.include_router(api_router)
    app.mount("/mcp", MCPAuthMiddleware(mcp_app))

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    return app


logging.basicConfig(level=logging.INFO)
for name in ("httpx", "httpcore", "uvicorn.access"):
    logging.getLogger(name).addFilter(_RedactAuthFilter())


def app_factory() -> FastAPI:
    return create_app()
