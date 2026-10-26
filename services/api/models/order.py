"""Order data contract (docs/data-contract.md). Field for field, so a store adapter that drifts
fails validation instead of feeding the pipeline something it may not cite.

Nullable fields have no default on purpose: the contract says they are always present in the JSON,
with null when they do not apply.
"""

from typing import Self

from pydantic import EmailStr, Field, field_validator, model_validator

from services.api.models.common import StrictModel, UtcDatetime
from services.api.models.enums import (
    CancelledBy,
    CourierEventStatus,
    ItemFlag,
    OrderStatus,
    PaymentMethod,
    RefundMethod,
    ShippingTier,
)
from services.api.policy_constants import constant


def _normalise_email(value: str) -> str:
    return value.strip().lower()


class Item(StrictModel):
    item_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    unit_price_paise: int = Field(ge=0)
    item_flags: list[ItemFlag]
    discount_applied_paise: int = Field(ge=0)
    store_credit_used_paise: int = Field(ge=0)

    @property
    def line_amount_paise(self) -> int:
        """The item price paid (REF-2.4, REF-2.5): price times quantity, less the discount."""
        return self.unit_price_paise * self.quantity - self.discount_applied_paise

    @model_validator(mode="after")
    def _amounts_are_consistent(self) -> Self:
        if self.line_amount_paise < 0:
            raise ValueError("discount_applied_paise exceeds the line total")
        if len(set(self.item_flags)) != len(self.item_flags):
            raise ValueError("item_flags contains a duplicate")
        return self


class DamageReport(StrictModel):
    reported_at: UtcDatetime
    has_photo: bool
    description: str


class RefundRecord(StrictModel):
    refund_id: str = Field(min_length=1)
    issued_at: UtcDatetime
    amount_paise: int = Field(gt=0)
    method: RefundMethod


class CancellationRecord(StrictModel):
    cancelled_at: UtcDatetime
    cancelled_by: CancelledBy
    reason: str


class CourierEvent(StrictModel):
    """Extension (assumption A3): optional, and never turned into a promise."""

    occurred_at: UtcDatetime
    status: CourierEventStatus
    location: str | None
    note: str | None


class Order(StrictModel):
    order_id: str = Field(min_length=1)
    customer_email: EmailStr
    status: OrderStatus
    placed_at: UtcDatetime
    dispatched_at: UtcDatetime | None
    delivered_at: UtcDatetime | None
    payment_method: PaymentMethod
    order_value_paise: int = Field(ge=0)
    shipping_charge_paise: int = Field(ge=0)
    shipping_tier: ShippingTier
    items: list[Item] = Field(min_length=1)
    cancellation_record: CancellationRecord | None
    refund_record: RefundRecord | None
    damage_report: DamageReport | None
    courier_tracking_number: str | None
    courier_eta: UtcDatetime | None
    failed_delivery_attempts: int = Field(ge=0, le=3)
    courier_events: list[CourierEvent] = Field(default_factory=list)

    @field_validator("customer_email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalise_email(value)

    @model_validator(mode="after")
    def _items_and_value(self) -> Self:
        ids = [item.item_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("item_id must be unique within the order")
        total = sum(item.line_amount_paise for item in self.items)
        if self.order_value_paise != total:
            raise ValueError(
                f"order_value_paise ({self.order_value_paise}) must equal the sum of the item "
                f"line amounts ({total}), excluding shipping (assumption A1)"
            )
        return self

    @model_validator(mode="after")
    def _status_combinations(self) -> Self:
        status = self.status
        if status is OrderStatus.PLACED:
            self._require(self.dispatched_at is None and self.delivered_at is None, "placed")
        elif status is OrderStatus.DISPATCHED:
            self._require(
                self.dispatched_at is not None and self.delivered_at is None, "dispatched"
            )
        elif status is OrderStatus.DELIVERED:
            self._require(
                self.dispatched_at is not None and self.delivered_at is not None, "delivered"
            )
            if self.dispatched_at and self.delivered_at and self.delivered_at < self.dispatched_at:
                raise ValueError("delivered_at is before dispatched_at")
        elif status is OrderStatus.CANCELLED:
            self._require(self.delivered_at is None, "cancelled")
        elif status is OrderStatus.FAILED_DELIVERY:
            self._require(
                self.dispatched_at is not None
                and self.delivered_at is None
                and self.failed_delivery_attempts == 3,
                "failed_delivery",
            )
        return self

    @model_validator(mode="after")
    def _dependent_records(self) -> Self:
        if (self.cancellation_record is not None) != (self.status is OrderStatus.CANCELLED):
            raise ValueError("cancellation_record is set exactly when status is cancelled")
        if self.damage_report is not None and self.delivered_at is None:
            raise ValueError("damage_report needs delivered_at")
        if self.courier_eta is not None and self.status is not OrderStatus.DISPATCHED:
            raise ValueError("courier_eta is only allowed while status is dispatched")
        if self.payment_method is PaymentMethod.COD and self.order_value_paise > constant(
            "cod_limit_paise"
        ):
            raise ValueError("a COD order cannot exceed cod_limit_paise (PAY-7.1)")
        times = [event.occurred_at for event in self.courier_events]
        if times != sorted(times):
            raise ValueError("courier_events must be ordered oldest first")
        return self

    @staticmethod
    def _require(condition: bool, status: str) -> None:
        if not condition:
            raise ValueError(f"dispatched_at / delivered_at do not fit status {status!r}")


class CustomerHistory(StrictModel):
    customer_email: EmailStr
    total_orders: int = Field(ge=0)
    prior_complaint_count: int = Field(ge=0)
    last_order_id: str | None

    @field_validator("customer_email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalise_email(value)

    @model_validator(mode="after")
    def _last_order_matches_count(self) -> Self:
        if (self.total_orders == 0) != (self.last_order_id is None):
            raise ValueError("last_order_id is null exactly when total_orders is 0")
        return self
