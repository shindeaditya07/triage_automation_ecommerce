"""The validator's maintained lists, read from grounding_config.yaml (never hard-coded here)."""

import os
import re
from functools import lru_cache
from pathlib import Path
from re import Pattern

import yaml
from pydantic import Field, field_validator

from services.api.models.common import StrictModel

SIGN_OFF_PLACEHOLDER = "{sign_off}"
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "grounding_config.yaml"


def _compile(patterns: list[str]) -> list[Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


class GroundingConfig(StrictModel):
    max_reply_chars: int = Field(gt=0)
    commitment_patterns: list[str] = Field(min_length=1)
    apology_patterns: list[str] = Field(min_length=1)
    obedience_patterns: list[str] = Field(min_length=1)
    causal_patterns: list[str]
    process_terms: list[str]
    product_claim_phrases: list[str]
    couriers: list[str]
    allowed_names: list[str]
    order_id_patterns: list[str] = Field(min_length=1)
    tracking_patterns: list[str]
    allowed_emails: list[str] = Field(default_factory=list)
    fallback_template: str = Field(min_length=1)

    @field_validator(
        "commitment_patterns",
        "apology_patterns",
        "obedience_patterns",
        "causal_patterns",
        "order_id_patterns",
        "tracking_patterns",
    )
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        try:
            _compile(patterns)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from exc
        return patterns

    @field_validator("fallback_template")
    @classmethod
    def _template_ends_with_the_sign_off(cls, template: str) -> str:
        if template.count(SIGN_OFF_PLACEHOLDER) != 1 or not template.rstrip().endswith(
            SIGN_OFF_PLACEHOLDER
        ):
            raise ValueError(f"must end with {SIGN_OFF_PLACEHOLDER}, once")
        return template

    def fallback_text(self, sign_off: str) -> str:
        """The safe reply used after two failed attempts, with the store's sign-off filled in."""
        return self.fallback_template.rstrip().replace(SIGN_OFF_PLACEHOLDER, sign_off)

    def compiled(self, name: str) -> list[Pattern[str]]:
        return _compile(getattr(self, name))


def load_config(path: Path | None = None) -> GroundingConfig:
    chosen = path or Path(os.environ.get("GROUNDING_CONFIG_PATH") or DEFAULT_PATH)
    return GroundingConfig.model_validate(yaml.safe_load(chosen.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_config() -> GroundingConfig:
    return load_config()
