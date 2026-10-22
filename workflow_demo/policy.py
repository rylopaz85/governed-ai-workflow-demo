"""The model's suggestion cannot grant itself authority to execute actions."""

from dataclasses import dataclass
import re
import unicodedata

from .models import Classification

REVIEW_TERMS = ("refund", "payment", "delete", "account access", "legal", "personal data")

# Security resources are deliberately conservative: even an informational
# mention goes to review rather than relying on a classifier to infer intent.
# Match whole tokens so "accounting", "cataloging" and "administered" stay safe.
ACCOUNT_SECURITY_TERMS = re.compile(
    r"\b(?:accounts?|logins?|logons?|signins?|signons?|log (?:in|on)|sign (?:in|on)|"
    r"passwords?|passcodes?|credentials?|permissions?|privileges?|rights|access|"
    r"authentications?|authorizations?|authorisations?|authenticators?|"
    r"mfa|2fa|sso|rbac|acls?|roles?|admins?|administrators?|superusers?|"
    r"(?:api|security|recovery) keys?|(?:security|recovery|verification) (?:tokens?|codes?)|"
    r"group memberships?|two factor verification)\b"
)

# Lifecycle/membership requests sometimes name only a user or group, without
# saying "account", "login" or "permission". Require both a subject and change
# language here, allowing ordinary user guides and group pricing inquiries.
IDENTITY_SUBJECTS = re.compile(
    r"\b(?:users?|members?|groups?|profiles?|identit(?:y|ies)|owners?|sessions?)\b"
)
IDENTITY_CHANGES = re.compile(
    r"\b(?:add(?:s|ed|ing)?|assign(?:s|ed|ing|ment)?|"
    r"remov(?:e[sd]?|ing|al)|chang(?:e[sd]?|ing)|"
    r"creat(?:e[sd]?|ing|ion)|updat(?:e[sd]?|ing)|"
    r"modif(?:y|ies|ied|ying|ication)|"
    r"(?:de|re)?activat(?:e[sd]?|ing|ion)|"
    r"(?:en|dis)abl(?:e[sd]?|ing)|"
    r"(?:un)?lock(?:s|ed|ing|out)?|block(?:s|ed|ing)?|"
    r"suspend(?:s|ed|ing)?|suspension|"
    r"grant(?:s|ed|ing)?|revok(?:e[sd]?|ing)|revocation|"
    r"reset(?:s|ting)?|restor(?:e[sd]?|ing|ation)|"
    r"(?:pro|de)mot(?:e[sd]?|ing|ion)|mak(?:e[sd]?|ing)|"
    r"terminat(?:e[sd]?|ing|ion)|invalidat(?:e[sd]?|ing|ion)|"
    r"(?:de)?provision(?:s|ed|ing)?|(?:on|off)board(?:s|ed|ing)?|"
    r"(?:log|sign)\b.*\b(?:out|off))\b"
)


def _account_security_request(message: str) -> bool:
    # Normalize case, compatibility characters and separators, including
    # sign-in, LOG_IN, apostrophes and repeated/newline whitespace.
    normalized = " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", message).casefold()))
    return bool(
        ACCOUNT_SECURITY_TERMS.search(normalized)
        or (IDENTITY_SUBJECTS.search(normalized) and IDENTITY_CHANGES.search(normalized))
    )


@dataclass(frozen=True)
class PolicyResult:
    requires_approval: bool
    reason: str


def evaluate(message: str, recommendation: Classification, classifier_failed: bool) -> PolicyResult:
    if classifier_failed:
        return PolicyResult(True, "classifier_unavailable")
    if any(term in message.lower() for term in REVIEW_TERMS) or _account_security_request(message):
        return PolicyResult(True, "high_impact_request")
    if recommendation.urgency == "high":
        return PolicyResult(True, "high_urgency")
    if recommendation.confidence < 0.75:
        return PolicyResult(True, "low_confidence")
    return PolicyResult(False, "low_risk_route")
