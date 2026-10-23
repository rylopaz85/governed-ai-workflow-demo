# Governed AI Workflow Automation | Portfolio Work Sample

A standalone, runnable technical work sample prepared for an **AI & Workflow Automation Specialist** application with AI-assisted coding. This repository illustrates how to join webhook/API engineering, AI-assisted routing, deterministic approval policy, durable workflow state, audit history and retry handling in one workflow.

**Scope:** A local demonstration using **synthetic leads** and a **mock CRM**. Not deployed for a paying customer, not a production service, and not an extract of any proprietary AI platform.

## Why this demo exists

Inbound requests are often routed manually, and unreliable automation can trigger duplicate actions, lose context, or expose data. This sample converts a signed lead intake request into a traceable workflow:

1. Verify HMAC-signed API webhook with a bounded timestamp window (signature also binds the idempotency key).
2. Validate input and deduplicate retries with a persistent idempotency key.
3. Classify requests using an **offline rules fixture** or an **optional OpenAI API adapter**. The default rules mode is *not* an LLM.
4. Apply deterministic policy. Sensitive/high-urgency/low-confidence cases wait for an authorized human.
5. Queue permitted work in a durable SQLite outbox, then dispatch to a **mock CRM**, with retry/backoff and audit history.

The classifier never grants its own permission to perform consequential actions. Sequential webhook retries are checked before calling the optional paid LLM, and the database enforces uniqueness for concurrent retries. See [architecture](docs/ARCHITECTURE.md) and [security review](docs/SECURITY.md).

## Stack

Python 3.11+, FastAPI, Pydantic v2, SQLite, HTTPX (optional LLM adapter), Pytest, GitHub Actions. No hosted service required for the default demo.

## Quick start

From a command prompt, create a virtual environment and install dependencies:

```bash
python -m venv .venv
# PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m scripts.demo_walkthrough
```

The walkthrough executes **two complete synthetic scenarios without network access**, prints their status transitions and audit actions, and cleans up its temporary database. No API key, account, or employer data is required.

To run the actual local HTTP server, generate **two distinct random secrets**. Example for PowerShell:

```powershell
$env:WEBHOOK_SECRET = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:OPERATOR_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:AI_BACKEND = "rules"
```

Start the server:

```bash
python -m workflow_demo.api
```

In a **second terminal**, provide the **same webhook secret** to that process (for example by setting it there independently or via a private local secret store), then:

```bash
python scripts/send_example.py
```

Open <http://127.0.0.1:8000/docs> for interactive API documentation. You can query a workflow using `GET /v1/workflows/{id}` with `Authorization: Bearer <OPERATOR_TOKEN>`, approve/reject review cases, and call `POST /v1/outbox/drain` to **simulate** dispatch. Do not share your secrets or commit a populated `.env` file.

### Try the human-approval path

In `scripts/send_example.py`, change the synthetic message to:

```text
Urgent: please change account access immediately.
```

Rerun the script and note `status=awaiting_approval`. The dispatcher cannot process this request until the operator explicitly approves it. A rejection is terminal. Every decision leaves an audit record, accessible to the authenticated operator.

Non-urgent requests such as `Please disable this user's login` also require approval,
even when the classifier returns normal urgency and high confidence. The deterministic
gate reviews account/authentication and permission language, plus user/group lifecycle
changes. It normalizes case and separators such as `sign-in` and `LOG_IN`. Security
information requests are conservatively reviewed too; ordinary pricing, user-guide,
and notification requests can still use the low-risk route. See the policy's
[scope and limitations](docs/SECURITY.md#account-and-permission-review).

### Optional real AI adapter

Set `AI_BACKEND=openai` and configure `OPENAI_API_KEY` and `OPENAI_MODEL` with a model supporting strict JSON schema outputs. This enables the real LLM classifier; the default mode does **not** call OpenAI or any remote service. The adapter uses basic email/phone redaction before sending text, but this is insufficient for actual customer PII. **Use synthetic inputs only.** API calls may incur usage charges. If the provider fails, the request is placed in manual review.

## Evidence and engineering trade-offs

The automated test suite covers webhook auth, schema validation, timestamp checks, idempotency/conflict, conservative AI failure behavior, human approvals/rejections, mock delivery, retry/backoff, and data minimization. CI runs the suite for each push and pull request.

This demonstrates a **technical implementation**, not claims about past deployments, a historical n8n/Make/Zapier mastery level, or quantified production savings. The architecture page explicitly identifies its limitations.

## Application summary (3–5 sentences)

> As a technical work sample, I am sharing a locally tested Python automation service featuring authenticated webhook intake, input validation, deduplication, and a replaceable classification layer. Its deterministic policy requires human approval for sensitive or low-confidence cases rather than letting an AI recommendation directly authorize actions. The demo includes persistent workflow state, a retryable outbox, audit history, and a mock CRM adapter that avoids external side effects. Automated tests, an architecture diagram, and documented production limitations make the implementation easy to inspect and reproduce.

**Provenance:** This sample was prepared with AI coding assistance. It is not a client deployment or a substitute for prior work experience. Before representing it as a personal project, the applicant should personally review the implementation, run it, understand the design trade-offs, and be ready to explain or modify it during an assessment.
