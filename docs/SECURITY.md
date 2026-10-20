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
