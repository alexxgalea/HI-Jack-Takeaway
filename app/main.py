from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import admin, auth, orders, restaurants
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging


def create_app() -> FastAPI:
    settings = get_settings()  # fail fast if required settings (e.g. JWT_SECRET) are missing
    configure_logging()

    app = FastAPI(title="HI-Jack Takeaway")

    # AppError -> its own status with a `{"detail": ...}` body; anything
    # unhandled -> a generic 500 with the traceback in the log, not the body.
    register_exception_handlers(app)

    # Order matters: the last middleware added is the outermost one, so CORS
    # wraps the request log. A preflight is then answered by CORS without
    # being logged as a request the app served, which is what it is.
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Off, and the reason the default origin list can be `["*"]`: this API
        # authenticates with a Bearer header the client attaches deliberately,
        # never with a cookie the browser would send on its own. With
        # credentials enabled a wildcard would be both invalid per the spec and
        # a standing invitation to cross-site requests.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(restaurants.router)
    app.include_router(restaurants.items_router)
    app.include_router(orders.router)
    app.include_router(admin.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
