"""Mock store: serves the seeded orders in the shape docs/data-contract.md defines."""

from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query

from services.api.models import CustomerHistory, Order

from .clock import current_time
from .store import Lookup, Store


def create_app(now: datetime | None = None) -> FastAPI:
    """Build the app. `now` defaults to FROZEN_NOW, or the real time when that is unset."""
    store = Store.from_clock(now or current_time())
    app = FastAPI(title="ReplyDesk Mock Store", version="0.2.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/orders/{order_id}", response_model=Order)
    def get_order(order_id: str, email: Annotated[str, Query(min_length=3)]) -> Order:
        result, order = store.order_for(order_id, email)
        if result is Lookup.NOT_FOUND:
            raise HTTPException(status_code=404, detail="order not found")
        if result is Lookup.WRONG_CUSTOMER or order is None:
            raise HTTPException(status_code=403, detail="order does not belong to this customer")
        return order

    @app.get("/customers/{email}/history", response_model=CustomerHistory)
    def get_history(email: str) -> CustomerHistory:
        history = store.history_for(email)
        if history is None:
            raise HTTPException(status_code=404, detail="customer not found")
        return history

    return app


app = create_app()
