"""FastAPI application factory (Phase 1: minimal skeleton)."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Poker Learning Assistant API", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app
