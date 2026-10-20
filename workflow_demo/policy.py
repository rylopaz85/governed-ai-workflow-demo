"""The model's suggestion cannot grant itself authority to execute actions."""

from dataclasses import dataclass
from .models import Classification

REVIEW_TERMS = ("refund", "payment", "delete", "account access", "legal", "personal data")


@dataclass(frozen=True)
class PolicyResult:
    requires_approval: bool
    reason: str


def evaluate(message: str, recommendation: Classification, classifier_failed: bool) -> PolicyResult:
    if classifier_failed:
        return PolicyResult(True, "classifier_unavailable")
    if any(term in message.lower() for term in REVIEW_TERMS):
        return PolicyResult(True, "high_impact_request")
    if recommendation.urgency == "high":
        return PolicyResult(True, "high_urgency")
    if recommendation.confidence < 0.75:
        return PolicyResult(True, "low_confidence")
    return PolicyResult(False, "low_risk_route")
