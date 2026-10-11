"""Minimal control-plane service; business routes will be introduced later."""

from fastapi import FastAPI

app = FastAPI(
    title="Metriqen Control API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/health/live", tags=["operations"])
def live() -> dict[str, str]:
    return {"service": "metriqen-control-api", "status": "ok"}


@app.get("/health/ready", tags=["operations"])
def ready() -> dict[str, str]:
    # There are no external dependencies to check yet.
    return {"service": "metriqen-control-api", "status": "ready"}
