"""Minimal auditable inbound-lead workflow: HMAC -> classifier -> policy -> outbox."""

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from .classifiers import Classifier, OpenAIClassifier, RulesClassifier
from .models import Classification, IntakeResponse, LeadRequest
from .policy import evaluate
from .store import Conflict, MockCRM, NotFound, WorkflowStore, drain_outbox

MAX_BODY_BYTES = 16 * 1024
MAX_CLOCK_SKEW_SECONDS = 5 * 60
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


@dataclass(frozen=True)
class Settings:
    webhook_secret: str
    operator_token: str
    db_path: str
    ai_backend: str = "rules"
    openai_api_key: str | None = None
    openai_model: str | None = None

    @classmethod
    def from_env(cls):
        webhook = os.environ.get("WEBHOOK_SECRET", "")
        token = os.environ.get("OPERATOR_TOKEN", "")
        if len(webhook) < 16 or len(token) < 16:
            raise ValueError("WEBHOOK_SECRET and OPERATOR_TOKEN must each contain at least 16 characters")
        return cls(
            webhook_secret=webhook,
            operator_token=token,
            db_path=os.environ.get("WORKFLOW_DB_PATH", "workflow-demo.sqlite3"),
            ai_backend=os.environ.get("AI_BACKEND", "rules"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            openai_model=os.environ.get("OPENAI_MODEL"),
        )


def build_classifier(settings: Settings) -> Classifier:
    if settings.ai_backend == "rules":
        return RulesClassifier()
    if settings.ai_backend == "openai":
        return OpenAIClassifier(settings.openai_api_key or "", settings.openai_model or "")
    raise ValueError("AI_BACKEND must be 'rules' or 'openai'")


def verify_signature(body: bytes, timestamp: str | None, signature: str | None, secret: str, idempotency_key: str, now: float):
    try:
        parsed = int(timestamp or "")
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "invalid webhook authentication") from exc
    if abs(now - parsed) > MAX_CLOCK_SKEW_SECONDS:
        raise HTTPException(401, "invalid webhook authentication")
    signed_message = f"{timestamp}.{idempotency_key}.".encode() + body
    expected = "sha256=" + hmac.new(secret.encode(), signed_message, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "invalid webhook authentication")


def create_app(
    settings: Settings,
    store: WorkflowStore | None = None,
    classifier: Classifier | None = None,
    dispatcher: MockCRM | None = None,
    clock=time.time,
) -> FastAPI:
    if len(settings.webhook_secret) < 16 or len(settings.operator_token) < 16:
        raise ValueError("weak/missing credentials")
    if settings.ai_backend not in ("rules", "openai"):
        raise ValueError("unsupported classifier")
    store = store or WorkflowStore(settings.db_path, clock=clock)
    classifier = classifier or build_classifier(settings)
    dispatcher = dispatcher or MockCRM()

    app = FastAPI(
        title="Governed Lead Automation Portfolio Demo",
        description="Local demonstration only. Does not contact a real CRM.",
        version="1.0.0",
    )

    def operator_required(authorization: str | None = Header(default=None)):
        expected = "Bearer " + settings.operator_token
        if authorization is None or not hmac.compare_digest(expected, authorization):
            raise HTTPException(401, "operator authentication required")

    @app.get("/healthz")
    def health():
        return {"status": "ok", "mode": "mock_only", "classifier": settings.ai_backend}

    @app.post("/v1/webhooks/leads", response_model=IntakeResponse)
    async def intake(
        request: Request,
        x_webhook_timestamp: str | None = Header(default=None),
        x_webhook_signature: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        if idempotency_key is None or IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
            raise HTTPException(400, "invalid idempotency key")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                raise HTTPException(413, "body too large")
        verify_signature(bytes(body), x_webhook_timestamp, x_webhook_signature, settings.webhook_secret, idempotency_key, clock())
        try:
            lead = LeadRequest.model_validate_json(bytes(body))
        except ValidationError as exc:
            raise HTTPException(422, "invalid lead payload") from exc
        canonical = json.dumps(lead.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        # Avoid repeated LLM calls/cost on ordinary retries. The transaction in
        # create_or_replay remains the authority for concurrent callers.
        try:
            existing = store.find_by_key(idempotency_key, digest)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        if existing:
            return IntakeResponse(
                workflow_id=existing["workflow_id"],
                status=existing["status"],
                department=existing["department"],
                urgency=existing["urgency"],
                requires_approval=existing["status"] == "awaiting_approval",
                replayed=True,
            )
        classifier_failed = False
        try:
            suggestion = classifier.classify(lead.message)
            # Validate even when a custom injected classifier is used.
            suggestion = Classification.model_validate(suggestion)
            classifier_name = settings.ai_backend
        except Exception:
            # A failed classifier must never grant autonomous execution rights.
            suggestion = Classification(department="operations", urgency="normal", confidence=0.0)
            classifier_name = "fallback"
            classifier_failed = True
        decision = evaluate(lead.message, suggestion, classifier_failed)
        try:
            workflow, replayed = store.create_or_replay(
                lead, idempotency_key, digest, suggestion, classifier_name, decision
            )
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return IntakeResponse(
            workflow_id=workflow["workflow_id"],
            status=workflow["status"],
            department=workflow["department"],
            urgency=workflow["urgency"],
            requires_approval=workflow["status"] == "awaiting_approval",
            replayed=replayed,
        )

    @app.get("/v1/workflows/{workflow_id}", dependencies=[Depends(operator_required)])
    def get_workflow(workflow_id: str):
        try:
            return {"workflow": store.get(workflow_id), "audit_events": store.events(workflow_id)}
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/v1/workflows/{workflow_id}/approve", dependencies=[Depends(operator_required)])
    def approve(workflow_id: str):
        try:
            return store.decide(workflow_id, approved=True)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/workflows/{workflow_id}/reject", dependencies=[Depends(operator_required)])
    def reject(workflow_id: str):
        try:
            return store.decide(workflow_id, approved=False)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/outbox/drain", dependencies=[Depends(operator_required)])
    def dispatch_pending():
        return drain_outbox(store, dispatcher, limit=5)

    return app


def main():
    import uvicorn

    settings = Settings.from_env()
    app = create_app(settings)
    # Local-only by default. Do NOT expose this demo to the public internet.
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)


if __name__ == "__main__":
    main()
