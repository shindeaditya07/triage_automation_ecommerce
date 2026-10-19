# ReplyDesk: Order Data Contract

**Version:** 1.0 (2026-10-19)
**Source:** `spec.md` v1.1 section 15, with the assumptions listed at the end of this file.

The mock store (`services/mock-store`) and any future real-store adapter must return exactly these shapes. The pipeline and the grounding validator may cite **only fields that exist here**; a claim about anything else is ungrounded. Pydantic models for these shapes are written in `feature/p1-db-schema` and `feature/p1-mock-store` and must match this file field for field.

## Conventions

| Rule | Detail |
|---|---|
| Timestamps | ISO-8601 in **UTC**, timezone-aware, with a trailing `Z` (for example `2026-10-15T12:00:00Z`). Never naive. |
| Dates in IST | Never stored. Policy windows that use the IST delivery date (RET-1.1) are computed from `delivered_at` in `services/api/time_utils.py`. |
| Money | **Integer paise.** Never floats. Rupees appear only at the API edge or in the UI. |
| IDs and emails | `order_id` is a case-sensitive string. `customer_email` is lower-cased and trimmed before it is stored or compared. |
| Nullability | A field marked `X \| null` is always present in the JSON, with `null` when it does not apply. Fields are never omitted. |
| Unknown fields | Rejected by the models (`extra="forbid"`), so a typo cannot slip through. |
| No raw payment data | Card numbers are never stored. UPI IDs and bank account numbers are masked before storage (REF-2.3, spec section 16). |

## Order

| Field | Type | Notes |
|---|---|---|
| `order_id` | string | Unique. |
| `customer_email` | string | Key used for sender verification (TON-9.4). |
| `status` | enum | `placed` \| `dispatched` \| `delivered` \| `cancelled` \| `failed_delivery` |
| `placed_at` | UTC timestamp | Payment confirmation time (basis for SHP-4.1 dispatch estimate). |
| `dispatched_at` | UTC timestamp \| null | Set when the order left the warehouse and has a tracking number. |
| `delivered_at` | UTC timestamp \| null | The courier-confirmed delivery time. Basis for RET-1.1, DMG-3.1 and PRD-6.3. |
| `payment_method` | enum | `upi` \| `card` \| `netbanking` \| `cod` |
| `order_value_paise` | integer | See assumption A1. Used for ESC-8.1, SHP-4.3 and PAY-7.1. |
| `shipping_charge_paise` | integer | `0` when shipping was free (SHP-4.3). |
| `shipping_tier` | enum | `metro` \| `standard` \| `remote`, from the delivery pin code at order time (SHP-4.2). |
| `items` | array of Item | At least one. |
| `cancellation_record` | CancellationRecord \| null | |
| `refund_record` | RefundRecord \| null | |
| `damage_report` | DamageReport \| null | |
| `courier_tracking_number` | string \| null | |
| `courier_eta` | UTC timestamp \| null | Only when the courier has given a new ETA (SHP-4.5). The store never invents one. |
| `failed_delivery_attempts` | integer | `0` to `3`. |
| `courier_events` | array of CourierEvent | Extension, see assumption A3. May be empty. Oldest first. |

### Allowed combinations (validated by the models)

| `status` | `dispatched_at` | `delivered_at` | other |
|---|---|---|---|
| `placed` | null | null | |
| `dispatched` | set | null | |
| `delivered` | set | set | `delivered_at` is not before `dispatched_at` |
| `cancelled` | any | null | `cancellation_record` is set |
| `failed_delivery` | set | null | `failed_delivery_attempts` is `3` |

Also: `cancellation_record` is null unless `status` is `cancelled`; `damage_report` is null unless `delivered_at` is set; `courier_eta` is null unless `status` is `dispatched`; `payment_method = cod` implies `order_value_paise` at or below the `cod_limit_paise` constant (PAY-7.1).

## Item (one or more per order)

| Field | Type | Notes |
|---|---|---|
| `item_id` | string | Unique within the order. |
| `product_name` | string | |
| `quantity` | integer | At least 1. |
| `unit_price_paise` | integer | Price of one unit before discount. |
| `item_flags` | array of enum | Any of `final_sale` \| `consumable` \| `personalized` \| `gift_card` \| `small_appliance`. Empty when none apply. Drives RET-1.4 and PRD-6.3. |
| `discount_applied_paise` | integer | Total discount on this line (all units). `0` if none. |
| `store_credit_used_paise` | integer | Store credit applied to this line. `0` if none. Refunded as store credit, not cash (REF-2.5). |

Line amount = `unit_price_paise * quantity - discount_applied_paise`, never negative. This is "the item price paid" in REF-2.4 and REF-2.5.

## DamageReport (null or object)

| Field | Type | Notes |
|---|---|---|
| `reported_at` | UTC timestamp | Compared with `delivered_at` against `damage_window_hours` (DMG-3.1). |
| `has_photo` | boolean | `false` means DMG-3.4 applies: ask for photos, offer nothing. |
| `description` | string | Customer's words. Untrusted text (spec section 16). |

## RefundRecord (null or object)

| Field | Type | Notes |
|---|---|---|
| `refund_id` | string | |
| `issued_at` | UTC timestamp | Date and amount may be quoted in a reply (REF-2.6). |
| `amount_paise` | integer | Greater than 0. |
| `method` | enum | `original_payment` \| `upi` \| `neft` \| `store_credit` (REF-2.3, REF-2.5). |

## CancellationRecord (null or object)

| Field | Type | Notes |
|---|---|---|
| `cancelled_at` | UTC timestamp | |
| `cancelled_by` | enum | `customer` \| `store` |
| `reason` | string | |

## CourierEvent (extension, optional)

| Field | Type | Notes |
|---|---|---|
| `occurred_at` | UTC timestamp | |
| `status` | enum | `picked_up` \| `in_transit` \| `out_for_delivery` \| `delivery_attempted` \| `delivered` \| `returned_to_warehouse` |
| `location` | string \| null | City or hub name. |
| `note` | string \| null | Courier's free text. |

Replies may quote the latest event as "the courier's latest status" (SHP-4.5). They may not turn an event into a promise.

## CustomerHistory

| Field | Type | Notes |
|---|---|---|
| `customer_email` | string | Key. |
| `total_orders` | integer | |
| `prior_complaint_count` | integer | Earlier tickets of type complaint. Not the same as the per-thread repeat count in ESC-8.2. |
| `last_order_id` | string \| null | Null for a customer with no orders. |

## Mock-store endpoints

| Endpoint | Returns |
|---|---|
| `GET /orders/{order_id}?email=<sender>` | An **Order**, or `404` if the id does not exist, or `403` if it belongs to a different email. A mismatch returns no order data (TON-9.4). |
| `GET /customers/{email}/history` | A **CustomerHistory**, or `404`. |
| `GET /health` | `{"status": "ok"}` |

## Assumptions to confirm

These are my reading of the spec where it is silent. Change them here before the models are written.

- **A1. `order_value_paise` is the sum of item line amounts, excluding shipping.** Items minus discounts, before shipping and before store credit. The policy says "orders totalling ₹999" (SHP-4.3), "order value above ₹10,000" (ESC-8.1) and "orders totalling ₹5,000" (PAY-7.1) without saying whether shipping counts. This is the choice that makes free shipping not depend on itself.
- **A2. `shipping_tier` is stored, not derived.** The mock store sets it; the real adapter maps pin codes to it.
- **A3. `courier_events` is not in spec section 15.** It is here because the branch list asks for a CourierEvent schema. It is optional and can be dropped without affecting any other field.
- **A4. Refund `method` values and `cancelled_by` values** are filled in from REF-2.3 and CAN-5.1. The spec gives the field names only.
