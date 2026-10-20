import json

import httpx

from workflow_demo.classifiers import OpenAIClassifier, redact_contact_info
from workflow_demo.models import Classification
from workflow_demo.policy import evaluate


def test_contact_redaction():
    cleaned = redact_contact_info("Email jane@example.com or call 602-555-0178")
    assert "jane@example.com" not in cleaned
    assert "602-555-0178" not in cleaned
    assert "[EMAIL]" in cleaned and "[PHONE]" in cleaned


def test_llm_adapter_schema_validation_and_redaction():
    requests = []

    def handle(req: httpx.Request):
        requests.append(req)
        data = json.loads(req.content)
        user_text = data["messages"][1]["content"]
        assert "jane@example.com" not in user_text
        assert data["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({
                "department": "sales", "urgency": "normal", "confidence": 0.9
            })}}]
        })

    client = httpx.Client(transport=httpx.MockTransport(handle))
    classifier = OpenAIClassifier("fake-key", "configured-model", client=client)
    result = classifier.classify("Please send pricing to jane@example.com")
    assert result == Classification(department="sales", urgency="normal", confidence=0.9)
    assert len(requests) == 1


def test_policy_not_controlled_by_model_confidence_alone():
    suggested = Classification(department="sales", urgency="normal", confidence=0.99)
    assert evaluate("Delete customer personal data", suggested, False).requires_approval is True
    assert evaluate("ordinary inquiry", suggested, True).requires_approval is True
    assert evaluate("ordinary inquiry", suggested, False).requires_approval is False
