from fastapi import FastAPI

from app.api.routers import auth
from app.core.config import get_settings


def create_app() -> FastAPI:
    get_settings()  # fail fast if required settings (e.g. JWT_SECRET) are missing

    app = FastAPI(title="HI-Jack Takeaway")

    app.include_router(auth.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
