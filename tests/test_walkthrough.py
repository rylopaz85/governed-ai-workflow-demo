import json
import socket

from scripts.demo_walkthrough import main


def test_walkthrough_preserves_offline_approval_and_mock_dispatch(monkeypatch, capsys):
    def refuse_network(*args, **kwargs):
        raise AssertionError("The offline walkthrough must not connect to a network")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["safe_request_initial_state"] == "queued"
    assert result["sensitive_request_initial_state"] == "awaiting_approval"
    assert result["approval_required"] is True
    assert result["safe_mock_dispatch_count"] == 1
    assert result["approved_mock_dispatch_count"] == 1
    assert result["final_sensitive_state"] == "dispatched_mock"
    assert result["sensitive_audit_actions"] == [
        "intake_accepted", "route_selected", "approved", "dispatch_attempt", "dispatched_mock",
    ]
