# Security review notes

| Boundary | Control in sample | Remaining risk / mitigation needed |
|---|---|---|
| Internet webhook | HMAC-SHA256 signature, timestamp window, body length cap, strict schema | Per-IP rate limits, TLS, upstream replay registry, gateway controls |
| Caller retry | Durable idempotency key + canonical input digest | Database durability/backups and dedicated replay-retention policy |
| LLM suggestion | Deterministic policy; fail closed to human review | Prompt-injection evals, stronger redaction, model-level monitoring |
| Operator actions | Separate bearer token, audit events | Production SSO, MFA, roles, credential rotation, attribution |
| Downstream effects | Mock CRM only; manually initiated queue drain | Real CRM credentials and recipient-level idempotency |
| Retry safety | SQLite transaction, leased outbox, bounded exponential backoff | Fencing tokens, dead-letter queue, alerting, distributed workers |
| Data privacy | No raw lead text retained in SQLite | Privacy notice, retention schedule, robust PII handling |

The example is deliberately safe to run **locally** and should not be exposed directly to the internet. It includes no actual customer data, credentials, proprietary platform source, internal infrastructure topology, or production endpoints.

## Account and permission review

The deterministic policy checks the original request independently of the
classifier's department, urgency, or confidence. Account, login, password,
credential, permission, role, administrator, and related security-resource
language requires human approval. Change language naming a user, member, group,
profile, identity, owner, or session also requires review. A classification with
normal urgency and confidence 1.0 cannot override these rules. Classifier errors
or invalid responses continue to fail closed with `classifier_unavailable`.

Security-resource mentions are deliberately reviewed even when informational.
The rules normalize Unicode compatibility characters, case, whitespace, and
punctuation, and use whole tokens to avoid matching words such as `accounting`
or `cataloging`. The existing financial, deletion, legal, and personal-data
rules and audit reason codes are preserved. A newly reviewed workflow has no
outbox row until an authenticated approval; rejection is terminal.

This remains a conservative English-language demonstration policy, not a
complete natural-language authorization system. Unrecognized paraphrases,
other languages, and adversarial obfuscation can exceed its vocabulary. A real
action connector needs structured action/resource authorization and default
review for unknown operations before execution.

The corrected policy applies to new intake. Existing workflows and idempotent
replays retain their stored decisions; raw messages are intentionally not kept
for re-evaluation. When re-running a synthetic scenario after changing policy,
use a fresh temporary demo database. This change does not migrate or reclassify
previously queued work.
