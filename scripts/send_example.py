"""Send a signed, synthetic test lead. Requires WEBHOOK_SECRET."""
import hashlib
import hmac
import json
import os
import time
import urllib.request
import uuid

secret = os.environ["WEBHOOK_SECRET"]
body = json.dumps({
    "lead_ref": "lead_demo_001",
    "source": "website",
    "message": "Please send pricing for a workflow automation demo.",
}).encode()
timestamp = str(int(time.time()))
idempotency_key = str(uuid.uuid4())
signature = hmac.new(secret.encode(), (timestamp + "." + idempotency_key + ".").encode() + body, hashlib.sha256).hexdigest()
request = urllib.request.Request(
    "http://127.0.0.1:8000/v1/webhooks/leads",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature": "sha256=" + signature,
        "Idempotency-Key": idempotency_key,
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.read().decode())
