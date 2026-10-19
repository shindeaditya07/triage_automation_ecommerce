"""ReplyDesk API: scaffold stub. Real endpoints arrive in later branches."""

from fastapi import FastAPI

app = FastAPI(title="ReplyDesk API", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Never touches the database."""
    return {"status": "ok"}
