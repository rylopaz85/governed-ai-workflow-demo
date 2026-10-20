"""One-command no-network demonstration for portfolio reviewers.

Run from repository root: python -m scripts.demo_walkthrough
"""

import hashlib
import hmac
import json
import secrets
import tempfile
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from workflow_demo.api import Settings, create_app


def send(client: TestClient, secret: str, message: str):
    payload = {"lead_ref": "synthetic_" + uuid.uuid4().hex[:12], "source": "website", "message": message}
    raw = json.dumps(payload).encode()
    now = str(int(time.time()))
    key = str(uuid.uuid4())
    signature = hmac.new(secret.encode(), (now + "." + key + ".").encode() + raw, hashlib.sha256).hexdigest()
    response = client.post("/v1/webhooks/leads", content=raw, headers={
        "X-Webhook-Timestamp": now,
        "X-Webhook-Signature": "sha256=" + signature,
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    })
    response.raise_for_status()
    return response.json()


def main():
    with tempfile.TemporaryDirectory(prefix="governed-workflow-demo-") as folder:
        secret = secrets.token_urlsafe(32)
        token = secrets.token_urlsafe(32)
        settings = Settings(secret, token, str(Path(folder) / "demo.sqlite3"))
        headers = {"Authorization": "Bearer " + token}

        with TestClient(create_app(settings)) as client:
            standard = send(client, secret, "Could you provide a quote for a new demo?")
            sensitive = send(client, secret, "Urgent: refund a payment and change account access.")
            assert standard["status"] == "queued"
            assert sensitive["status"] == "awaiting_approval"

            initial = client.post("/v1/outbox/drain", headers=headers)
            initial.raise_for_status()
            assert initial.json()["succeeded"] == 1

            approval = client.post(
                f"/v1/workflows/{sensitive['workflow_id']}/approve", headers=headers
            )
            approval.raise_for_status()
            assert approval.json()["status"] == "queued"

            final = client.post("/v1/outbox/drain", headers=headers)
            final.raise_for_status()
            assert final.json()["succeeded"] == 1

            record = client.get(
                f"/v1/workflows/{sensitive['workflow_id']}", headers=headers
            )
            record.raise_for_status()
            actions = [event["action"] for event in record.json()["audit_events"]]
            assert "approved" in actions and "dispatched_mock" in actions

            print(json.dumps({
                "safe_request_initial_state": standard["status"],
                "sensitive_request_initial_state": sensitive["status"],
                "approval_required": sensitive["requires_approval"],
                "safe_mock_dispatch_count": initial.json()["succeeded"],
                "approved_mock_dispatch_count": final.json()["succeeded"],
                "final_sensitive_state": record.json()["workflow"]["status"],
                "sensitive_audit_actions": actions,
                "note": "Synthetic inputs only; no real CRM or LLM contacted",
            }, indent=2))


if __name__ == "__main__":
    main()
