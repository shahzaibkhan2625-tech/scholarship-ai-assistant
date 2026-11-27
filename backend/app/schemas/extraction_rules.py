"""Declarative deterministic extraction rules (003 US5, FR-RULES-1/5, research R-11).

Stored under `source_registry.extraction_rules['deterministic']` and written only by the operator command
(`app.services.source_rules`); the LLM never authors or activates rules (FR-RULES-5). The schema is closed
(`extra="forbid"`): there is deliberately no category-exclusion field (plan decision 7), so a rule set cannot
quietly drop part of a listing.
"""

from typing import Self

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["DeterministicRules", "NameRule", "UrlRule", "rules_for_source"]


def _check_selector(selector: str) -> None:
    try:
        BeautifulSoup("<div></div>", "html.parser").select(selector)
    except Exception as exc:  # noqa: BLE001 - soupsieve raises several types for a bad selector
        raise ValueError(f"invalid CSS selector {selector!r}") from exc


class NameRule(BaseModel):
    """The item's name: read from an attribute of the item element, or from the text of a selector inside it."""

    model_config = ConfigDict(extra="forbid")

    attribute: str | None = Field(default=None, min_length=1)
    selector: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Self:
        if (self.attribute is None) == (self.selector is None):
            raise ValueError("name needs exactly one of `attribute` or `selector`")
        if self.selector is not None:
            _check_selector(self.selector)
        return self


class UrlRule(BaseModel):
    """The detail link: an attribute (default `href`) of the element matched by `selector` inside the item
    (or of the item itself when no selector is given)."""

    model_config = ConfigDict(extra="forbid")

    selector: str | None = Field(default=None, min_length=1)
    attribute: str = Field(default="href", min_length=1)

    @model_validator(mode="after")
    def _valid_selector(self) -> Self:
        if self.selector is not None:
            _check_selector(self.selector)
        return self


class DeterministicRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    enabled: bool = False
    item_selector: str = Field(min_length=1)
    name: NameRule
    detail_url: UrlRule | None = None
    provider_from_registry: bool = False
    min_items: int = Field(default=1, ge=1)
    # Per rule set; the defaults are `RULES_ANOMALY_LOW_RATIO` / `RULES_ANOMALY_HIGH_RATIO` (settings).
    anomaly_low_ratio: float | None = Field(default=None, gt=0, le=1)
    anomaly_high_ratio: float | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.version != 1:
            raise ValueError("unsupported rules version (only 1)")
        _check_selector(self.item_selector)
        return self


def rules_for_source(extraction_rules: dict | None) -> DeterministicRules | None:
    """The source's ENABLED rule set, or `None` (no rules, or present but not enabled): the caller then makes no
    use of the rule engine at all."""
    raw = (extraction_rules or {}).get("deterministic")
    if not raw:
        return None
    rules = DeterministicRules.model_validate(raw)
    return rules if rules.enabled else None
