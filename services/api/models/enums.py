"""Closed value sets shared by the Pydantic models and the database CHECK constraints."""

from enum import StrEnum


class Channel(StrEnum):
    EMAIL = "email"
    FORM = "form"


class TicketStatus(StrEnum):
    NEW = "new"
    CLASSIFIED = "classified"
    DRAFTED = "drafted"
    ESCALATED = "escalated"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"
    FAILED = "failed"


class Intent(StrEnum):
    ORDER_STATUS = "order_status"
    REFUND = "refund"
    RETURN = "return"
    DAMAGED_ITEM = "damaged_item"
    PRODUCT_QUESTION = "product_question"
    CANCELLATION = "cancellation"
    ADDRESS_CHANGE = "address_change"
    PAYMENT = "payment"
    WARRANTY = "warranty"
    OTHER = "other"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class Sentiment(StrEnum):
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    VERY_NEGATIVE = "very_negative"


class Flag(StrEnum):
    ANGRY = "angry"
    ABUSIVE = "abusive"
    LEGAL_THREAT = "legal_threat"
    SUSPECTED_FRAUD = "suspected_fraud"
    PROMPT_INJECTION_SUSPECTED = "prompt_injection_suspected"
    REPEAT_COMPLAINT = "repeat_complaint"
    ORDER_MISMATCH = "order_mismatch"
    COMPENSATION_REQUEST = "compensation_request"
    HIGH_VALUE = "high_value"


class SourceType(StrEnum):
    ORDER_FIELD = "order_field"
    POLICY = "policy"


class ProposedAction(StrEnum):
    NONE = "none"
    REQUEST_INFORMATION = "request_information"
    RETURN_INSTRUCTIONS_SUGGESTED = "return_instructions_suggested"
    CANCELLATION_SUGGESTED = "cancellation_suggested"
    ADDRESS_CHANGE_SUGGESTED = "address_change_suggested"
    STORE_CREDIT_SUGGESTED = "store_credit_suggested"
    REFUND_SUGGESTED = "refund_suggested"
    REPLACEMENT_SUGGESTED = "replacement_suggested"
    ESCALATE = "escalate"


class DraftKind(StrEnum):
    LLM = "llm"
    FALLBACK_TEMPLATE = "fallback_template"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EscalationStage(StrEnum):
    PRE_LOOKUP = "pre_lookup"
    POST_LOOKUP = "post_lookup"


class JobType(StrEnum):
    PROCESS_TICKET = "process_ticket"
    RETENTION_SWEEP = "retention_sweep"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
