"""Which policy passages every reply to a given kind of ticket must be able to see.

These are the passages the answer depends on whatever the vector search happens to rank, so a
damage report always carries the photo rule and the report window (spec FR-8). The IDs are
checked against `docs/policies.md` when retrieval runs: renaming a policy ID is a breaking change
(docs/policies.md, notes for the development team), and this fails loudly instead of quietly
dropping a passage.
"""

from collections.abc import Iterable

from services.api.models.enums import Intent
from services.api.policy_source import expectation

# Applies to every reply: state policy, never promise an outcome (TON-9.2).
ALWAYS: tuple[str, ...] = ("TON-9.2",)

BY_INTENT: dict[Intent, tuple[str, ...]] = {
    Intent.ORDER_STATUS: ("SHP-4.1", "SHP-4.2", "SHP-4.4", "SHP-4.5", "SHP-4.7"),
    Intent.REFUND: ("REF-2.1", "REF-2.2", "REF-2.3", "REF-2.4", "REF-2.5", "REF-2.6"),
    Intent.RETURN: ("RET-1.1", "RET-1.2", "RET-1.3", "RET-1.4", "RET-1.5"),
    Intent.DAMAGED_ITEM: ("DMG-3.1", "DMG-3.2", "DMG-3.3", "DMG-3.4", "RET-1.3"),
    Intent.PRODUCT_QUESTION: ("PRD-6.1",),
    Intent.CANCELLATION: ("CAN-5.1", "CAN-5.2", "CAN-5.3"),
    Intent.ADDRESS_CHANGE: ("SHP-4.6", "CAN-5.3", "FAQ-10.2"),
    Intent.PAYMENT: ("PAY-7.1", "PAY-7.2", "PAY-7.3"),
    Intent.WARRANTY: ("PRD-6.3",),
    Intent.OTHER: (),
}

# A suggestion-only draft (ESC-8.3) must be able to say the owner decides.
SUGGESTION_ONLY: tuple[str, ...] = ("ESC-8.3",)


def deterministic_ids(intent: Intent, extra: Iterable[str] = ()) -> list[str]:
    """The passages always retrieved for `intent`, then `extra`, without repeats."""
    seen: dict[str, None] = {}
    for policy_id in (*ALWAYS, *BY_INTENT[intent], *extra):
        seen.setdefault(policy_id)
    return list(seen)


def unknown_policy_ids(ids: Iterable[str]) -> list[str]:
    """IDs that are not passages in the loaded `docs/policies.md`. A draft may cite only IDs that
    exist, so the grounding step uses this too."""
    known = expectation().policy_ids
    return sorted({policy_id for policy_id in ids if policy_id not in known})
