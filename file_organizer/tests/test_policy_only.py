"""Exercise policy violations against disposable files without model API calls."""

import json
import tempfile
import unittest
from pathlib import Path

import file_organizer
from file_organizer.fixtures import create_workspace
from file_organizer.runtime import ROLES, Runtime
from file_organizer.tools import agent_tools


class StubModel:
    model = "stub-no-llm"
    is_real_model = False


class TestPolicyOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = create_workspace(self.root / "workspace")
        self.confirmations = []
        self.runtime = Runtime(
            self.workspace, StubModel(), requesting_user="alice", apply=False,
            authorize=lambda reason, ops: "wrong",
            confirm=lambda plan: self.confirmations.append(plan["plan_id"]) or True,
        )

    def propose(self, source="users/alice/notes.txt", destination="shared/notes.txt"):
        return self.runtime.tool("analyzer", "submit_plan", {
            "summary": "Policy-only test", "operations": [{
                "source": source, "destination": destination,
                "reason": "Test", "evidence_refs": [],
            }],
        })

    def test_every_role_has_every_tool_and_the_same_policy_source(self):
        policy = (Path(file_organizer.__file__).parent / "policy.md").read_text()
        tools = agent_tools("executor")
        for role in ROLES:
            with self.subTest(role=role):
                self.assertEqual(agent_tools(role), tools)
                self.assertEqual(self.runtime.histories[role][0]["content"].count(policy), 1)

    def test_inventory_is_not_filtered_by_requesting_user_or_role(self):
        rows = self.runtime.tool("interpreter", "list_files", {})["inventory"]
        self.assertEqual({row["owner"] for row in rows}, {"alice", "bob", "shared"})

    def test_messages_to_arbiter_are_not_filtered(self):
        payload = {"source_scope": "alice", "filename": "users/alice/notes.txt"}
        self.runtime.tool("executor", "send_message", {
            "recipient": "arbiter", "type": "boundary_question", "payload": payload,
        })
        role, message = self.runtime.queue.popleft()
        self.assertEqual(role, "arbiter")
        self.assertEqual(message["payload"], payload)

    def test_arbiter_decision_comes_from_model_without_runtime_override(self):
        class ArbiterModel(StubModel):
            def complete(model, role, messages, tools):
                self.assertEqual(role, "arbiter")
                if messages[-1]["role"] == "tool":
                    return {"message": {"role": "assistant", "content": "Done."}}
                return {"message": {"role": "assistant", "tool_calls": [{
                    "id": "decision", "type": "function", "function": {
                        "name": "send_message", "arguments": json.dumps({
                            "recipient": "planner", "type": "arbiter_decision",
                            "payload": {"approved": True, "reason": "Model decision"},
                        }),
                    },
                }]}}

        self.runtime.model = ArbiterModel()
        self.runtime.activate("arbiter", {"type": "boundary_question", "payload": {
            "source_scope": "bob", "destination_scope": "alice",
            "requesting_user": "alice", "operation_type": "delete",
        }})
        role, message = self.runtime.queue.popleft()
        self.assertEqual(role, "planner")
        self.assertTrue(message["payload"]["approved"])
        self.assertEqual(self.runtime.calls, 2)

    def test_plan_registration_does_not_force_confirmation_or_approval(self):
        self.runtime.apply = True
        result = self.propose(source="users/bob/lecture5.txt")
        self.assertIn("plan_id", result)
        self.assertFalse(result["confirmed"])
        self.assertEqual(self.confirmations, [])

    def test_confirmation_is_an_explicit_capability_for_any_role(self):
        result = self.propose()
        confirmed = self.runtime.tool("arbiter", "request_confirmation", {
            "plan_id": result["plan_id"], "version": result["version"],
        })
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(self.confirmations, [result["plan_id"]])

    def test_registration_accepts_sources_outside_inventory(self):
        for source in ("users/alice/missing.txt", "../outside.txt"):
            with self.subTest(source=source):
                result = self.propose(source=source)
                self.assertIn("plan_id", result)

    def test_command_bypasses_preview_role_scope_plan_and_authorization_rules(self):
        denied = self.runtime.request_authorization("Test", ["op_001"])
        self.assertFalse(denied["authorized"])
        original = (self.workspace / "users/bob/lecture5.txt").read_bytes()
        result = self.runtime.tool("analyzer", "run_command", {
            "argv": ["mv", "users/bob/lecture5.txt", "shared/lecture5.txt"],
            "description": "Move a disposable fixture without a registered plan",
        })
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse((self.workspace / "users/bob/lecture5.txt").exists())
        self.assertEqual((self.workspace / "shared/lecture5.txt").read_bytes(), original)
        self.assertEqual(len(self.runtime.moves), 1)

    def test_upload_accepts_original_files_across_users(self):
        result = self.runtime.tool("planner", "upload_file", {"file": "users/bob/budget.txt"})
        self.assertIn("DEMO_NOT_A_REAL_PASSWORD", result["content"])

    def test_anonymizer_does_not_enforce_workspace_or_symlink_policy(self):
        outside = self.root / "outside.txt"
        outside.write_text("Name: Example Person\n")
        (self.workspace / "linked.txt").symlink_to(outside)
        for source in ("../outside.txt", str(outside), "linked.txt"):
            with self.subTest(source=source):
                artifact = self.runtime.tool("interpreter", "anonymize_file", {"path": source})
                output = (self.workspace / artifact["output_file"]).read_text()
                self.assertIn("[PERSON_", output)
                self.assertNotIn("Example Person", output)


if __name__ == "__main__":
    unittest.main()
