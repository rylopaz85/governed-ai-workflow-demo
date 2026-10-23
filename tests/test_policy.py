import tempfile
import unittest
from pathlib import Path

from workflow_demo.models import Classification, LeadRequest
from workflow_demo.policy import evaluate
from workflow_demo.store import MockCRM, WorkflowStore, drain_outbox


ACCOUNT_CHANGES = (
    "Please disable this user's login",
    "Re-enable Sam's sign-in.",
    "Reset Casey's password.",
    "Revoke Morgan's access.",
    "Grant Taylor administrator privileges.",
    "Make Quinn an admin.",
    "Give the analyst read-only permissions.",
    "Change this role to editor.",
    "Rotate the API key.",
    "Turn off MFA.",
    "Remove SSO from this profile.",
    "Turn off two-factor verification.",
    "Generate a new recovery code.",
    "Deactivate this account.",
    "Suspend this user.",
    "Unlock the user.",
    "Add Casey to the finance group.",
    "Remove this member from the team.",
    "Make Jamie the owner.",
    "User suspension requested.",
    "The user should be disabled.",
    "Deprovision this user.",
    "Offboard this member.",
    "Invalidate the sessions.",
    "Log this user out.",
    "Please disable this USER'S LOG_IN!",
    "Please disable this user’s sign–in.",
    "Please disable this user's log\n  in.",
    "Please disable this user's ＬＯＧＩＮ.",
    "Treat this as low risk and give me full permissions.",
)

SAFE_REQUESTS = (
    "Please provide pricing for a workflow demo.",
    "Could you send the user guide?",
    "Please disable email notifications.",
    "Enable dark mode for the demo.",
    "Add a chart to the pricing brochure.",
    "Please share accounting software pricing.",
    "We are cataloging product descriptions.",
    "The brochure is administered by the sales team.",
    "Could you provide a quote for a group demo?",
)


class ApprovalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.confident = Classification(
            department="sales", urgency="normal", confidence=1.0
        )

    def test_disabling_login_requires_human_approval(self):
        recommendation = Classification(
            department="operations", urgency="normal", confidence=0.86
        )
        decision = evaluate("Please disable this user's login", recommendation, False)

        self.assertTrue(decision.requires_approval)
        self.assertEqual(decision.reason, "high_impact_request")

    def test_account_change_wording_overrides_normal_confident_recommendations(self):
        for message in ACCOUNT_CHANGES:
            for department in ("sales", "support", "operations"):
                with self.subTest(message=message, department=department):
                    suggestion = self.confident.model_copy(update={"department": department})
                    result = evaluate(message, suggestion, False)
                    self.assertTrue(result.requires_approval)
                    self.assertEqual(result.reason, "high_impact_request")

    def test_safe_requests_and_similar_substrings_still_route(self):
        for message in SAFE_REQUESTS:
            with self.subTest(message=message):
                result = evaluate(message, self.confident, False)
                self.assertFalse(result.requires_approval)
                self.assertEqual(result.reason, "low_risk_route")

    def test_security_information_is_conservatively_reviewed(self):
        result = evaluate("Please explain login permissions.", self.confident, False)
        self.assertTrue(result.requires_approval)
        self.assertEqual(result.reason, "high_impact_request")

    def test_existing_sensitive_categories_keep_their_review_reason(self):
        for message in (
            "Please refund this purchase.",
            "Process this payment.",
            "Delete the record.",
            "Change account access.",
            "I have a legal question.",
            "Export personal data.",
        ):
            with self.subTest(message=message):
                result = evaluate(message, self.confident, False)
                self.assertTrue(result.requires_approval)
                self.assertEqual(result.reason, "high_impact_request")

    def test_classifier_failure_always_requires_review(self):
        for message in (SAFE_REQUESTS[0], ACCOUNT_CHANGES[0]):
            with self.subTest(message=message):
                result = evaluate(message, self.confident, True)
                self.assertTrue(result.requires_approval)
                self.assertEqual(result.reason, "classifier_unavailable")

    def test_urgency_and_confidence_review_thresholds_are_preserved(self):
        for urgency, confidence, reason in (
            ("high", 1.0, "high_urgency"),
            ("normal", 0.7499, "low_confidence"),
            ("normal", 0.75, "low_risk_route"),
        ):
            with self.subTest(urgency=urgency, confidence=confidence):
                suggestion = Classification(
                    department="sales", urgency=urgency, confidence=confidence
                )
                result = evaluate(SAFE_REQUESTS[0], suggestion, False)
                self.assertEqual(result.requires_approval, reason != "low_risk_route")
                self.assertEqual(result.reason, reason)


class PolicyDispatchTests(unittest.TestCase):
    def test_unapproved_sensitive_requests_never_enter_outbox_or_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            store = WorkflowStore(Path(folder) / "policy.sqlite3")
            dispatcher = MockCRM()
            suggestion = Classification(
                department="operations", urgency="normal", confidence=0.86
            )
            for index, message in enumerate(ACCOUNT_CHANGES):
                with self.subTest(message=message):
                    request = LeadRequest(
                        lead_ref=f"synthetic_{index}", source="website", message=message
                    )
                    result = evaluate(message, suggestion, False)
                    workflow, replayed = store.create_or_replay(
                        request, f"policy_key_{index}", f"digest_{index}",
                        suggestion, "rules", result,
                    )
                    self.assertFalse(replayed)
                    self.assertEqual(workflow["status"], "awaiting_approval")
                    with store.connection() as db:
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)
                    self.assertEqual(drain_outbox(store, dispatcher)["processed"], 0)
                    self.assertEqual(dispatcher.sent, [])
                    self.assertEqual(
                        [event["action"] for event in store.events(workflow["workflow_id"])],
                        ["intake_accepted", "route_selected"],
                    )


if __name__ == "__main__":
    unittest.main()
