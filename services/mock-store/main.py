"""Mock store: scaffold stub. Orders and seed data arrive in feature/p1-mock-store."""

from fastapi import FastAPI

app = FastAPI(title="ReplyDesk Mock Store", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
