# Kaveri Home Goods: Customer Support Policies

> This is a **fictional** store used as the reference policy set for ReplyDesk development and evaluation.
> Each passage has a stable policy ID (for example `RET-1.1`). Drafted replies must cite these IDs.
> Currency is INR (₹). Policy version **1.1**, effective **2026-10-15**.
> **SHA-256 lock file:** CI must fail if this file changes without a version bump and eval rerun.

**About the store:** Kaveri Home Goods is a small online store selling kitchenware, home décor, and small appliances. It ships across India from a warehouse in Hyderabad. Support is handled by the store owner, Monday to Saturday, 10:00–18:00 IST.

> **For developers:** Do not hard-code any numeric value from this document. All numeric constants are sourced from `policy_constants.yaml`, keyed by policy ID. A CI test asserts every constant in that file matches the cited passage below.

---

## 1. Returns

**RET-1.1 Return window.**
Items can be returned within **7 calendar days of the delivery date** if they are unused, unwashed, and in original packaging with all tags and accessories. The window ends at 23:59:59 IST on the seventh calendar day after the delivery date (inclusive). Delivery date is determined from the courier-confirmed delivery timestamp converted to IST.

**RET-1.2 How to start a return.**
The customer must reply to the support thread with the order number and the reason for return. The owner reviews the request and, if eligible, sends return instructions. Returns sent without approval are not accepted.

**RET-1.3 Return shipping cost.**
- **Change of mind or wrong-size purchase:** the customer pays return shipping.
- **Wrong item, defective item, or damaged item (see section 3):** the store pays return shipping.

**RET-1.4 Non-returnable items.**
The following cannot be returned unless they arrive damaged or defective (sections 3 and 8):
- Items marked **Final Sale** on the product page at time of purchase
- Opened consumables (spice blends, candles, cleaning products)
- Personalized or custom-engraved items
- Gift cards

**RET-1.5 Returns after the window.**
Requests made after the 7-day window are not eligible under standard policy. They are reviewed by the owner individually. The customer must never be promised an exception or an outcome before the owner reviews.

---

## 2. Refunds

**REF-2.1 When a refund is issued.**
A refund is issued after the returned item is received and inspected at the warehouse, or, for a cancelled unshipped order, after the cancellation is confirmed. Every refund requires explicit owner approval.

**REF-2.2 Refund timeline.**
Approved refunds are processed within **5–7 business days** to the original payment method (where "business days" means Monday–Saturday, excluding public holidays on the configured holiday list). Banks may take additional time to post the credit. The store cannot accelerate the bank's processing.

**REF-2.3 Cash on delivery (COD) refunds.**
COD orders are refunded by bank transfer (UPI or NEFT) after the customer provides their account details through the support thread, or as store credit if the customer prefers. Account details (UPI ID, bank account numbers) shared in the thread are masked before storage and before any system processing.

**REF-2.4 Partial refunds.**
In a multi-item order, only the returned item's price is refunded. Original shipping charges are not refunded unless the return is due to a store error (wrong, defective, or damaged item).

**REF-2.5 Refund amount.**
The refund equals the item price paid, including any discount applied at purchase. Store credit or discount codes used in the original order are returned as store credit, not as cash.

**REF-2.6 Already refunded.**
If a refund has already been issued for a ticket, the reply may state the refund date and amount from the order record and remind the customer of the timeline in REF-2.2. No further refund is offered.

---

## 3. Damaged, Defective, or Wrong Items

**DMG-3.1 Reporting window.**
Damaged, defective, or wrong items must be reported within **48 hours of the delivery timestamp** (UTC-based, displayed in IST), with at least one clear photo of both the item and the outer packaging.

**DMG-3.2 Resolution.**
If the report meets DMG-3.1 (within 48 hours and photo provided), the owner may offer either a **replacement or a full refund** (including original shipping charges). Both options require explicit owner approval. The draft must not promise which option will be chosen.

**DMG-3.3 Reports after 48 hours.**
Reports made after the 48-hour window are reviewed by the owner individually. No resolution may be promised before review; the reply should acknowledge receipt and state the owner will follow up.

**DMG-3.4 Missing photos.**
If no photo attachment is present, the reply must ask the customer to send clear photos of both the item and the outer packaging. No resolution is offered or implied until photos are received. Neither replacement nor refund is mentioned until photos are confirmed.

---

## 4. Shipping and Delivery

**SHP-4.1 Dispatch time.**
Orders are dispatched within **1–2 business days** of payment confirmation. Orders placed on Sundays or on a public holiday (per the configured holiday list) are dispatched on the next business day. "Dispatched" means the order has left the warehouse and a tracking number has been assigned.

**SHP-4.2 Delivery estimates (after dispatch).**
| Zone | Estimate |
|---|---|
| Metro cities (Hyderabad, Bengaluru, Chennai, Mumbai, Delhi, Kolkata, Pune) | **3–5 business days** |
| Other locations in India | **5–8 business days** |
| Remote or restricted pin codes | Up to **10 business days** |

The zone is determined by the delivery pin code at time of order.

**SHP-4.3 Free shipping.**
Shipping is free on orders totalling **₹999 or above**. For orders below ₹999, a flat shipping charge of ₹60 applies.

**SHP-4.4 Tracking.**
A tracking number is emailed when the order is dispatched. Tracking updates made by the courier may take up to 24 hours to appear after dispatch.

**SHP-4.5 Delayed orders.**
If an order has not arrived within the estimate in SHP-4.2, the reply may share the latest tracking status from the order record and apologize for the delay. The reply must not promise a new delivery date unless the courier has provided a confirmed new ETA in the order record. If a courier ETA is in the record, it may be stated as the courier's estimate, not the store's promise.

**SHP-4.6 Wrong or incomplete address.**
The store is not responsible for orders delivered to an incorrect address entered by the customer at checkout. Address changes are possible only **before dispatch**, on request through the support thread, subject to operational feasibility. After dispatch, address changes are not possible.

**SHP-4.7 Failed delivery.**
After three consecutive failed delivery attempts, the courier returns the order to the warehouse. The store contacts the customer about re-shipping (shipping charges apply) or a refund minus the original shipping charge. This re-contact is handled by the owner.

---

## 5. Cancellations and Order Changes

**CAN-5.1 Cancel before dispatch.**
Orders can be cancelled free of charge **before they are dispatched**. The customer must request cancellation through the support thread. The owner confirms the cancellation; it is not automatic.

**CAN-5.2 Cancel after dispatch.**
Orders cannot be cancelled after dispatch. The customer may refuse delivery at the door, or return the item under the standard return policy (section 1) once delivered.

**CAN-5.3 Order changes.**
Changes to items, quantities, or addresses are not possible after the order is placed. The customer can cancel before dispatch (CAN-5.1) and place a new order.

---

## 6. Product Questions

**PRD-6.1 Product information.**
Replies to product questions may use only information from the product page or the FAQ in this file (section 10). If the answer is not available in either source, the reply must state that the owner will follow up, and the ticket is escalated. The draft must not invent specifications, materials, or compatibility claims.

**PRD-6.2 Stock availability.**
Stock levels are shown on the product page in real time. Specific restock dates are not promised; the reply may suggest the customer check the product page for updates.

**PRD-6.3 Warranty.**
Small appliances (items with the `small_appliance` flag) carry a **6-month manufacturer warranty** against manufacturing defects, starting from the delivery date. Warranty claims require the order number and a description or photo of the fault. Claims are reviewed by the owner; outcomes are not promised in a draft.

**PRD-6.4 Care instructions.**
Care instructions are printed on the packaging and on the product page. Replies may direct customers to these sources.

---

## 7. Payments

**PAY-7.1 Accepted methods and COD limit.**
Accepted payment methods: UPI, debit cards, credit cards, net banking, and cash on delivery (COD). COD is available only for orders totalling **₹5,000 or less**. COD orders above ₹5,000 cannot be placed.

**PAY-7.2 Failed payments.**
If a payment was deducted but no order was created (payment gateway failure), the amount is automatically reversed by the payment gateway or bank within 5–7 business days. The store cannot speed up or initiate this reversal; the reply should refer the customer to their bank.

**PAY-7.3 Invoices.**
A GST invoice is emailed at the time of dispatch confirmation. Customers may request a copy of their invoice through the support thread.

---

## 8. Escalation and Owner-Only Decisions

**ESC-8.1 High-value orders.**
All support tickets about orders with an order value above **₹10,000** must be sent to the owner for handling. No automated draft is sent to the customer.

**ESC-8.2 Always escalate — no resolution promise.**
The following always go directly to the owner. No automated or suggested resolution promise may appear in any reply:
- Legal threats, consumer court mentions, chargeback filings, or complaints to a regulator or consumer body
- Abusive or threatening language directed at the store or its staff
- Suspected fraud or a request that appears to target another customer's order or personal data
- Repeated complaints on the same order (3 or more distinct messages on the same thread)

**ESC-8.3 Outside standard policy — owner's decision required.**
The following are outside the store's automatic resolution authority. Replies must not promise any outcome; they must state the owner will review and respond:
- Return requests after the 7-day window (RET-1.5)
- Damage reports after the 48-hour window (DMG-3.3)
- Requests for compensation, goodwill gestures, or discount codes
- Refund requests for Final Sale items that are not damaged or defective
- Any situation not explicitly covered by sections 1–7 of this document

---

## 9. Communication Guidelines (Reply Tone)

**TON-9.1 Tone and structure.**
Replies are polite, concise, and written in plain language. Use the customer's first name when it is known. Apologize at most once and only where the store is at fault; do not over-apologize or use repeated apology phrases.

**TON-9.2 State policy, never promise outcomes.**
Replies must not promise refunds, replacements, compensation, re-delivery dates, or any other concrete outcome. They may state what the applicable policy provides (cited by policy ID) and say that the owner will confirm the specific outcome. Example of acceptable language: "Based on our policy (RET-1.1), returns within 7 days of delivery are eligible, and the owner will confirm the next steps."

**TON-9.3 Sign-off.**
Every reply must end with the sign-off exactly as configured in the store profile. Default: **"Kaveri Home Goods Support"**.

**TON-9.4 Privacy in replies.**
Replies must reference only order information that matches the sender's verified email address. If the order ID provided does not match the sender's email, the reply must not reveal any order details, and the ticket must be escalated.

---

## 10. Frequently Asked Questions

**FAQ-10.1 Do you ship internationally?**
No. Kaveri Home Goods currently ships within India only.

**FAQ-10.2 Can I change my delivery address after ordering?**
Address changes are possible only before dispatch, on request through the support thread. See SHP-4.6.

**FAQ-10.3 Do you offer exchanges?**
Exchanges are not offered directly. Customers may return an eligible item under section 1 and place a new order for the preferred item.

**FAQ-10.4 How do I track my order?**
Your tracking number is included in the dispatch confirmation email. See SHP-4.4 for details.

**FAQ-10.5 Do you offer gift wrapping?**
Yes. Gift wrapping costs ₹50 per order and must be selected at checkout. It cannot be added after the order is placed.

**FAQ-10.6 What are your support hours?**
Support is available Monday to Saturday, 10:00 to 18:00 IST. Messages received outside these hours are answered on the next working day.

**FAQ-10.7 Do you offer store credit?**
Yes. Refunds can be issued as store credit if the customer prefers. See REF-2.5 and REF-2.3.

**FAQ-10.8 Can I cancel an order I just placed?**
Yes, if the order has not been dispatched yet. See CAN-5.1.

---

## Notes for the Development Team

> This section is not part of the customer-facing policy.

- **Numeric constants** that the grounding validator uses must be sourced from `policy_constants.yaml`, not from this document. As of v1.1:
  - `return_window_days = 7` (RET-1.1)
  - `damage_window_hours = 48` (DMG-3.1)
  - `free_shipping_threshold_paise = 99900` (SHP-4.3, ₹999)
  - `shipping_fee_paise = 6000` (SHP-4.3, ₹60)
  - `high_value_threshold_paise = 1000000` (ESC-8.1, ₹10,000)
  - `cod_limit_paise = 500000` (PAY-7.1, ₹5,000)
  - `refund_business_days_min = 5` (REF-2.2)
  - `refund_business_days_max = 7` (REF-2.2)
  - `warranty_months = 6` (PRD-6.3)
  - `dispatch_window_days_min = 1` (SHP-4.1)
  - `dispatch_window_days_max = 2` (SHP-4.1)
  - Delivery zones and estimates: per SHP-4.2

- **Keep policy IDs stable.** The eval set and labeled tickets reference them as ground truth. Renaming a policy ID is a breaking change requiring eval re-labeling.
- **Versioning:** if this file changes for any reason, bump the version at the top, update the SHA-256 lock file, and re-run the evals. CI fails if the SHA-256 has changed without a version bump.
- **Business-day zone:** Monday–Saturday. Default holiday list: Gazetted public holidays of India 2026. Configurable.
