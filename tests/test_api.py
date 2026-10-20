import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from workflow_demo.api import Settings, create_app
from workflow_demo.models import Classification
from workflow_demo.store import MockCRM, WorkflowStore

FIXED_NOW = 1_800_000_000.0
WEBHOOK_SECRET = "secure-test-webhook-key-value"
OPERATOR_TOKEN = "secure-test-operator-token"


class FakeClock:
    def __init__(self):
        self.now = FIXED_NOW

    def __call__(self):
        return self.now


class FailClassifier:
    def classify(self, message):
        raise TimeoutError("test simulated provider error; do not log")


class LowConfidenceClassifier:
    def classify(self, message):
        return Classification(department="operations", urgency="normal", confidence=0.2)


@pytest.fixture
def setup(tmp_path):
    clock = FakeClock()
    settings = Settings(
        webhook_secret=WEBHOOK_SECRET,
        operator_token=OPERATOR_TOKEN,
        db_path=str(tmp_path / "test.sqlite3"),
    )
    store = WorkflowStore(settings.db_path, clock=clock)
    dispatcher = MockCRM()
    app = create_app(settings, store=store, dispatcher=dispatcher, clock=clock)
    with TestClient(app) as client:
        yield client, store, dispatcher, clock, settings


def lead(message="Please provide pricing for a new demo.", ref="lead_123"):
    return {"lead_ref": ref, "source": "website", "message": message}


def signed(client, payload=None, key="test_key_0001", now=FIXED_NOW, secret=WEBHOOK_SECRET):
    data = json.dumps(payload or lead()).encode()
    signature = hmac.new(secret.encode(), (str(int(now)) + "." + key + ".").encode() + data, hashlib.sha256).hexdigest()
    return client.post("/v1/webhooks/leads", content=data, headers={
        "X-Webhook-Timestamp": str(int(now)),
        "X-Webhook-Signature": "sha256=" + signature,
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    })


def authorized():
    return {"Authorization": "Bearer " + OPERATOR_TOKEN}


def test_signed_safe_lead_queues_and_dispatches_to_mock(setup):
    client, store, dispatcher, *_ = setup
    resp = signed(client)
    assert resp.status_code == 200
    j = resp.json()
    assert j["status"] == "queued"
    assert j["department"] == "sales"
    assert j["requires_approval"] is False
    assert client.post("/v1/outbox/drain").status_code == 401
    result = client.post("/v1/outbox/drain", headers=authorized())
    assert result.json() == {"processed": 1, "succeeded": 1, "failed": 0, "mode": "mock_only"}
    assert dispatcher.sent == [j["workflow_id"]]
    view = client.get(f"/v1/workflows/{j['workflow_id']}", headers=authorized()).json()
    assert view["workflow"]["status"] == "dispatched_mock"
    assert [e["action"] for e in view["audit_events"]][-1] == "dispatched_mock"


def test_signature_required_and_stale_timestamp(setup):
    client, *_ = setup
    assert client.post("/v1/webhooks/leads", json=lead(), headers={"Idempotency-Key": "test_key_0001"}).status_code == 401
    assert signed(client, now=FIXED_NOW - 301).status_code == 401
    assert signed(client, secret="attacker-secret-value-value").status_code == 401
    data = json.dumps(lead()).encode()
    key = "test_key_original"
    digest = hmac.new(WEBHOOK_SECRET.encode(),
                      (str(int(FIXED_NOW)) + "." + key + ".").encode() + data,
                      hashlib.sha256).hexdigest()
    # A captured signed body cannot be replayed under a different idempotency key.
    assert client.post("/v1/webhooks/leads", content=data, headers={
        "X-Webhook-Timestamp": str(int(FIXED_NOW)),
        "X-Webhook-Signature": "sha256=" + digest,
        "Idempotency-Key": "tampered_key_00001",
    }).status_code == 401


def test_duplicate_same_key_replays_without_duplicate_dispatch(setup):
    client, store, *_ = setup
    a = signed(client).json()
    b = signed(client).json()
    assert a["workflow_id"] == b["workflow_id"]
    assert b["replayed"] is True
    assert len(store.events(a["workflow_id"])) == 2
    assert client.post("/v1/outbox/drain", headers=authorized()).json()["processed"] == 1


def test_conflicting_idempotency_key_is_rejected(setup):
    client, *_ = setup
    assert signed(client).status_code == 200
    other = signed(client, lead("Urgent account access issue."))
    assert other.status_code == 409


def test_high_impact_requires_approval_and_reject_prevents_dispatch(setup):
    client, store, *_ = setup
    resp = signed(client, lead("Urgent: change account access immediately."))
    wid = resp.json()["workflow_id"]
    assert resp.json()["status"] == "awaiting_approval"
    assert client.post("/v1/outbox/drain", headers=authorized()).json()["processed"] == 0
    assert client.post(f"/v1/workflows/{wid}/reject", headers=authorized()).json()["status"] == "rejected"
    assert client.post(f"/v1/workflows/{wid}/approve", headers=authorized()).status_code == 409
    assert client.post("/v1/outbox/drain", headers=authorized()).json()["processed"] == 0


def test_approval_flow_and_operator_auth(setup):
    client, store, *_ = setup
    wid = signed(
        client, lead("Urgent outage; need support."), key="test_key_approval"
    ).json()["workflow_id"]
    assert client.post(f"/v1/workflows/{wid}/approve").status_code == 401
    assert client.post(f"/v1/workflows/{wid}/approve", headers=authorized()).json()["status"] == "queued"
    assert client.post(f"/v1/workflows/{wid}/approve", headers=authorized()).status_code == 409
    assert client.post("/v1/outbox/drain", headers=authorized()).json()["succeeded"] == 1


def test_classifier_failure_fails_closed(tmp_path):
    clock = FakeClock()
    settings = Settings(WEBHOOK_SECRET, OPERATOR_TOKEN, str(tmp_path / "fails.sqlite3"))
    app = create_app(settings, classifier=FailClassifier(), clock=clock)
    with TestClient(app) as client:
        resp = signed(client)
        assert resp.json()["status"] == "awaiting_approval"
        assert resp.json()["department"] == "operations"
        assert client.post("/v1/outbox/drain", headers=authorized()).json()["processed"] == 0


def test_low_confidence_fails_closed(tmp_path):
    clock = FakeClock()
    settings = Settings(WEBHOOK_SECRET, OPERATOR_TOKEN, str(tmp_path / "low.sqlite3"))
    app = create_app(settings, classifier=LowConfidenceClassifier(), clock=clock)
    with TestClient(app) as client:
        assert signed(client).json()["status"] == "awaiting_approval"


def test_dispatch_retry_and_backoff(setup):
    client, store, dispatcher, clock, *_ = setup
    wid = signed(client).json()["workflow_id"]
    dispatcher.fail_once_for.add(wid)
    first = client.post("/v1/outbox/drain", headers=authorized()).json()
    assert first["failed"] == 1
    assert client.post("/v1/outbox/drain", headers=authorized()).json()["processed"] == 0
    clock.now += 2.1
    second = client.post("/v1/outbox/drain", headers=authorized()).json()
    assert second["succeeded"] == 1
    assert dispatcher.sent == [wid]
    assert store.get(wid)["status"] == "dispatched_mock"


def test_raw_message_not_stored_in_sqlite(setup):
    client, store, _, _, settings = setup
    secret_phrase = "SECRET_EXAMPLE_DO_NOT_PERSIST"
    assert signed(client, lead("Please provide pricing " + secret_phrase)).status_code == 200
    assert secret_phrase.encode() not in open(settings.db_path, "rb").read()


def test_strict_payload_and_body_cap(setup):
    client, *_ = setup
    assert signed(client, {**lead(), "unexpected": True}).status_code == 422
    too_big = lead("x" * 20000)
    assert signed(client, too_big, key="test_key_large").status_code == 413


def test_health_and_details_do_not_expose_secrets(setup):
    client, *_ = setup
    response = client.get("/healthz")
    assert response.status_code == 200
    assert WEBHOOK_SECRET not in response.text and OPERATOR_TOKEN not in response.text
    assert client.get("/v1/workflows/does-not-exist", headers=authorized()).status_code == 404


class CountingClassifier:
    def __init__(self):
        self.count = 0

    def classify(self, message):
        self.count += 1
        return Classification(department="sales", urgency="normal", confidence=0.9)


def test_replay_does_not_recharge_llm_for_sequential_retry(tmp_path):
    clock = FakeClock()
    settings = Settings(WEBHOOK_SECRET, OPERATOR_TOKEN, str(tmp_path / "counter.sqlite3"))
    classifier = CountingClassifier()
    app = create_app(settings, classifier=classifier, clock=clock)
    with TestClient(app) as client:
        assert signed(client).json()["replayed"] is False
        assert signed(client).json()["replayed"] is True
        assert classifier.count == 1


def test_dispatch_stops_after_bounded_attempts(setup):
    client, store, dispatcher, clock, *_ = setup
    wid = signed(client).json()["workflow_id"]

    def always_fail(_):
        raise RuntimeError("mock failure")

    dispatcher.send = always_fail
    for delay in (0, 2.1, 4.1):
        clock.now += delay
        result = client.post("/v1/outbox/drain", headers=authorized()).json()
        assert result["failed"] == 1
    assert store.get(wid)["status"] == "dispatch_failed"
    clock.now += 100
    assert client.post("/v1/outbox/drain", headers=authorized()).json()["processed"] == 0
    assert store.events(wid)[-1]["action"] == "terminal_failure"
