import json
import os
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from downloads_organizer.demo import ScriptedDemoModel, create_fixture
from downloads_organizer.runtime import Runtime, inventory, undo_run
from downloads_organizer.tools import agent_tools, run_command
from downloads_organizer.cli import main


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "inbox"

    def run_fixture(self, scenario="normal", *, apply=True, accept=True):
        create_fixture(self.root, scenario)
        runtime = Runtime(self.root, ScriptedDemoModel(), apply=apply,
                          confirm=lambda plan: accept, max_calls=180, max_steps=100)
        state = runtime.run("Classify synthetic files and preserve every copy.")
        return runtime, state

    def events(self, runtime):
        return [json.loads(line) for line in (runtime.trace.directory / "trace.jsonl").read_text().splitlines()]

    def test_full_flow_preserves_content_and_uses_anonymizer_outputs(self):
        runtime, state = self.run_fixture()
        self.assertEqual(state["status"], "completed")
        self.assertFalse(state["real_llm"])
        self.assertTrue(state["contents_preserved"])
        self.assertEqual(len(state["moves"]), 5)
        self.assertTrue((self.root / "Duplicates/paper_copy.md").is_file())
        self.assertTrue((self.root / "unsupported.bin").is_file())
        events = self.events(runtime)
        agents = {e["agent"] for e in events if e["kind"] == "model_response"}
        self.assertEqual(agents, {"analyzer", "planner", "executor"})
        uploaded = [e["result"]["selected_file"] for e in events
                    if e["kind"] == "tool_result" and e["tool"] == "upload_file"]
        self.assertTrue(all(p.startswith(".anonymized/") for p in uploaded))
        requests = json.dumps([e for e in events if e["kind"] == "model_request"])
        for secret in ["Alice Example", "DEMO_NOT_A_REAL_PASSWORD", "DEMO_NOT_A_REAL_API_KEY"]:
            self.assertNotIn(secret, requests)

    def test_phase1_role_restrictions_are_prompt_policy_not_tool_filtering(self):
        create_fixture(self.root)
        runtime = Runtime(self.root, ScriptedDemoModel())
        self.assertEqual(agent_tools("analyzer"), agent_tools("executor"))
        self.assertEqual(agent_tools("planner"), agent_tools("executor"))
        for role in ["analyzer", "planner"]:
            with self.subTest(role=role):
                # Intentionally violate P02 on synthetic data. Phase 1 does
                # not block the action or silently route it to Executor.
                result = runtime.tool(role, "list_files", {})
                self.assertEqual(result["inventory"], inventory(self.root))
                self.assertEqual(runtime.tool(role, "run_command", {"argv": ["test", "-e", "receipt.txt"], "description": "Check whether the receipt exists without changing it."})["exit_code"], 0)
                self.assertIn("DEMO_NOT_A_REAL_PASSWORD", runtime.tool(role, "upload_file", {"file": "receipt.txt"})["content"])
        self.assertFalse(runtime.queue)
        runtime.tool("analyzer", "send_message", {"recipient": "executor", "type": "file_request",
                                                  "payload": {"kind": "content", "request_id": "policy_bypass"}})
        queued_role, message = runtime.queue.popleft()
        self.assertEqual(queued_role, "executor")
        self.assertEqual(message["sender"], "analyzer")
        self.assertEqual(message["recipient"], "executor")

    def test_inventory_and_content_make_both_complete_round_trips(self):
        runtime, state = self.run_fixture()
        events = self.events(runtime)
        messages = [e["message"] for e in events if e["kind"] == "agent_message"]
        kickoff = messages[0]
        self.assertNotIn("inventory", kickoff["payload"])
        first_request = next(e for e in events if e["kind"] == "model_request")
        for row in state["before"]:
            self.assertNotIn(row["path"], first_request["messages"][-1]["content"])
        for request_id in ["inventory_1", "content_1"]:
            exchange = [m for m in messages if m["payload"].get("request_id") == request_id]
            self.assertEqual([(m["sender"], m["recipient"], m["type"]) for m in exchange], [
                ("analyzer", "planner", "information_request"), ("planner", "executor", "file_request"),
                ("executor", "planner", "file_observation"), ("planner", "analyzer", "evidence_response")])
            self.assertEqual(exchange[0]["payload"], exchange[1]["payload"])
            self.assertEqual(exchange[2]["payload"], exchange[3]["payload"])
        content_request = next(m["payload"] for m in messages if m["sender"] == "analyzer"
                               and m["payload"].get("kind") == "content")
        requested_paths = {r["path"] for r in content_request["files"]}
        actual_reads = {json.loads(e["arguments"])["path"] for e in events
                        if e["kind"] == "tool_request" and e["name"] == "anonymize_file"}
        self.assertEqual(actual_reads, requested_paths)
        self.assertNotIn("receipt.txt", actual_reads)
        self.assertEqual(len(actual_reads & {"paper.md", "paper_copy.md"}), 1)
        report = next(m["payload"] for m in messages if m["type"] == "analysis_report")
        self.assertEqual(next(r for r in report["files"] if r["path"] == "receipt.txt")["classification_basis"], "filename")
        self.assertFalse(any({m["sender"], m["recipient"]} == {"analyzer", "executor"} for m in messages))
        for e in events:
            if e["kind"] == "tool_request" and e["name"] not in {"send_message", "submit_plan"}:
                self.assertEqual(e["agent"], "executor")
        execution = [m for m in messages if m["type"] in {"execution_request", "execution_feedback"}]
        self.assertEqual([(m["sender"], m["recipient"]) for m in execution],
                         [("planner", "executor"), ("executor", "planner")])

    def test_confirming_a_plan_does_not_automatically_dispatch_it(self):
        create_fixture(self.root)
        runtime = Runtime(self.root, ScriptedDemoModel(), apply=True, confirm=lambda plan: True)
        result = runtime.submit_plan("review", [{"source": "lecture3.txt", "destination": "Courses/lecture3.txt",
                                                "reason": "course", "evidence_refs": []}])
        self.assertTrue(result["confirmed"])
        self.assertFalse(runtime.queue)
        self.assertFalse(any(e["kind"] == "agent_message" for e in self.events(runtime)))
        self.assertTrue((self.root / "lecture3.txt").is_file())

    def test_executor_checks_registered_confirmation_instead_of_message_claims(self):
        create_fixture(self.root)
        for case in ["declined", "wrong_digest", "stale_plan"]:
            with self.subTest(case=case):
                runtime = Runtime(self.root, ScriptedDemoModel(), apply=True,
                                  confirm=lambda plan: case != "declined")
                op = {"source": "lecture3.txt", "destination": "Courses/lecture3.txt",
                      "reason": "course", "evidence_refs": []}
                result = runtime.submit_plan("first", [op])
                if case == "stale_plan":
                    runtime.submit_plan("new version", [op])
                runtime.send("planner", "executor", "execution_request", {
                    "plan_id": result["plan_id"], "version": result["version"],
                    "digest": "fabricated" if case == "wrong_digest" else result["digest"],
                    "confirmed": True, "pending_operation_ids": result["pending_operation_ids"]})
                role, message = runtime.queue.popleft()
                runtime.activate(role, message)
                events = self.events(runtime)
                self.assertTrue(any(e["kind"] == "tool_request" and e["name"] == "get_plan" for e in events))
                self.assertFalse(any(e["kind"] == "tool_request" and e["name"] == "run_command" for e in events))
                feedback = [e["message"] for e in events if e["kind"] == "agent_message"][-1]
                self.assertEqual(feedback["recipient"], "planner")
                self.assertTrue(feedback["payload"]["blocked"])
                self.assertTrue((self.root / "lecture3.txt").is_file())

    def test_empty_folder_needs_no_content_reads_or_execution(self):
        self.root.mkdir()
        runtime = Runtime(self.root, ScriptedDemoModel(), apply=True)
        state = runtime.run("organize")
        self.assertEqual(state["status"], "no_changes")
        self.assertEqual(state["moves"], [])
        self.assertFalse(any(e["kind"] == "tool_request" and e["name"] in {"upload_file", "anonymize_file"}
                             for e in self.events(runtime)))

    def test_conflict_replans_and_reconfirms_without_overwrite(self):
        runtime, state = self.run_fixture("conflict")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(len(state["plans"]), 2)
        self.assertTrue(all(p["confirmed"] for p in state["plans"]))
        self.assertIn("Different lecture version", (self.root / "Courses/CS239/lecture3.txt").read_text())
        self.assertIn("Multi-agent", (self.root / "Courses/CS239/lecture3_2.txt").read_text())
        messages = [e["message"] for e in self.events(runtime) if e["kind"] == "agent_message"]
        self.assertTrue(any(m["sender"] == "executor" and m["recipient"] == "planner" for m in messages))

    def test_preview_uses_executor_for_evidence_but_never_moves_files(self):
        runtime, state = self.run_fixture(apply=False)
        self.assertEqual(state["status"], "preview_ready")
        self.assertEqual(state["moves"], [])
        self.assertTrue((self.root / "lecture3.txt").is_file())
        events = self.events(runtime)
        self.assertTrue(any(e.get("agent") == "executor" for e in events))
        self.assertFalse(any(e["kind"] == "agent_message" and e["message"]["type"] == "execution_request"
                             for e in events))
        self.assertFalse(any(e["kind"] == "tool_request" and e["name"] == "get_plan" for e in events))

    def test_declined_confirmation_does_not_execute(self):
        runtime, state = self.run_fixture(accept=False)
        self.assertEqual(state["status"], "not_executed")
        self.assertEqual(state["moves"], [])
        self.assertFalse(state["plans"][0]["confirmed"])

    def test_phase1_raw_upload_is_not_silently_anonymized_or_blocked(self):
        create_fixture(self.root)
        runtime = Runtime(self.root, ScriptedDemoModel())
        raw = runtime.tool("executor", "upload_file", {"file": "receipt.txt"})
        self.assertIn("DEMO_NOT_A_REAL_PASSWORD", raw["content"])
        self.assertFalse(raw["provenance_enforced"])

    def test_move_undo_and_idempotent_second_undo(self):
        runtime, state = self.run_fixture()
        first = undo_run(runtime.trace.directory, lambda moves: True)
        self.assertEqual(first["status"], "undone")
        self.assertEqual(first["restored"], 5)
        self.assertEqual({r["path"] for r in inventory(self.root)}, {r["path"] for r in state["before"]})
        second = undo_run(runtime.trace.directory, lambda moves: True)
        self.assertEqual(second["restored"], 0)

    def test_undo_preserves_newly_occupied_original_path(self):
        runtime, _ = self.run_fixture()
        (self.root / "lecture3.txt").write_text("new local file")
        result = undo_run(runtime.trace.directory, lambda moves: True)
        self.assertEqual(result["status"], "partial_undo")
        self.assertEqual((self.root / "lecture3.txt").read_text(), "new local file")
        self.assertTrue((self.root / "Courses/CS239/lecture3.txt").is_file())

    def test_model_failure_keeps_a_failed_manifest(self):
        class BrokenModel:
            model, is_real_model = "broken-test", False

            def complete(self, *args):
                raise RuntimeError("model unavailable")

        create_fixture(self.root)
        runtime = Runtime(self.root, BrokenModel())
        with self.assertRaises(RuntimeError):
            runtime.run("test")
        saved = json.loads((runtime.trace.directory / "manifest.json").read_text())
        self.assertEqual(saved["status"], "failed")

    def test_subprocess_excludes_credentials_and_limits_output(self):
        self.root.mkdir()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "FAKE_TEST_ONLY", "NVIDIA_API_KEY": "NV_TEST_ONLY"}):
            result = run_command(self.root, [sys.executable, "-c",
                "import os; print('OPENAI_API_KEY' in os.environ, 'NVIDIA_API_KEY' in os.environ); print('x'*1000)"],
                description="Inspect test environment variables and exercise bounded output.", output_limit=80)
        self.assertTrue(result["stdout"].startswith("False False"))
        self.assertTrue(result["truncated"])
        timeout = run_command(self.root, [sys.executable, "-c", "import time; time.sleep(5)"], description="Exercise the subprocess timeout using a test-only sleeping process.", timeout=0.05)
        self.assertTrue(timeout["timed_out"])

    def test_call_budget_is_bounded_and_recorded(self):
        create_fixture(self.root)
        runtime = Runtime(self.root, ScriptedDemoModel(), max_calls=1)
        with self.assertRaises(RuntimeError):
            runtime.run("test")
        self.assertEqual(runtime.calls, 1)
        self.assertEqual(runtime.state["status"], "failed")

    def test_cli_preview_prints_plan_operations_without_network(self):
        create_fixture(self.root)
        output = io.StringIO()
        with patch("downloads_organizer.models.OpenAIModel", return_value=ScriptedDemoModel()), \
                redirect_stdout(output):
            code = main(["organize", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Proposed plan (preview only)", output.getvalue())
        self.assertIn('"destination": "Courses/CS239/lecture3.txt"', output.getvalue())
        self.assertTrue((self.root / "lecture3.txt").is_file())

    def test_agent_that_never_submits_a_plan_is_not_reported_successful(self):
        class NoPlanModel:
            model, is_real_model = "no-plan-test", False

            def complete(self, *args):
                return {"message": {"role": "assistant", "content": "No action."}, "usage": None}

        create_fixture(self.root)
        runtime = Runtime(self.root, NoPlanModel())
        self.assertEqual(runtime.run("test")["status"], "no_plan")
        self.assertEqual(runtime.calls, 2)
        self.assertEqual(sum(e["kind"] == "agent_reminder" for e in self.events(runtime)), 1)

    def test_text_only_reports_and_plans_need_a_real_tool_call_after_one_reminder(self):
        create_fixture(self.root)
        for role in ["analyzer", "planner"]:
            with self.subTest(role=role):
                class TextFirstModel:
                    model, is_real_model = "text-first-test", False
                    count = 0

                    def complete(self, agent, messages, tools):
                        self.count += 1
                        if self.count == 1 or self.count == 3:
                            return {"message": {"role": "assistant", "content": "Here is my report or plan."}, "usage": None}
                        self_reminder = messages[-1]["content"]
                        assert "Runtime reminder" in self_reminder
                        name = "send_message" if agent == "analyzer" else "submit_plan"
                        arguments = ({"recipient": "planner", "type": "analysis_report", "payload": {"files": []}}
                                     if agent == "analyzer" else {"summary": "Test plan", "operations": [{
                                         "source": "lecture3.txt", "destination": "Courses/lecture3.txt",
                                         "reason": "course", "evidence_refs": []}]})
                        return {"message": {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)}}]}, "usage": None}

                runtime = Runtime(self.root, TextFirstModel(), apply=False)
                runtime.activate(role, {"type": "test", "payload": {}})
                self.assertEqual(runtime.calls, 3)
                self.assertTrue((self.root / "lecture3.txt").is_file())
                events = self.events(runtime)
                self.assertEqual(sum(e["kind"] == "agent_reminder" for e in events), 1)
                self.assertEqual(sum(e["kind"] == "tool_request" for e in events), 1)
                if role == "analyzer":
                    self.assertEqual(len(runtime.queue), 1)
                    self.assertFalse(runtime.plans)
                else:
                    self.assertEqual(len(runtime.plans), 1)
                    self.assertFalse(runtime.plans[0]["confirmed"])
                    self.assertFalse(runtime.queue)

    def test_missing_routing_fields_are_reported_without_delivering_a_message(self):
        create_fixture(self.root)

        class MissingFieldsModel:
            model, is_real_model = "missing-fields-test", False
            count = 0

            def complete(self, agent, messages, tools):
                self.count += 1
                if self.count == 3:
                    return {"message": {"role": "assistant", "content": "Finished."}, "usage": None}
                arguments = {"payload": {"files": []}}
                if self.count == 2:
                    error = json.loads(messages[-1]["content"])
                    assert error["error"] == "missing_tool_arguments"
                    assert error["missing"] == ["recipient", "type"]
                    arguments.update(recipient="planner", type="analysis_report")
                return {"message": {"role": "assistant", "tool_calls": [{"id": f"call_{self.count}",
                    "type": "function", "function": {"name": "send_message", "arguments": json.dumps(arguments)}}]},
                    "usage": None}

        runtime = Runtime(self.root, MissingFieldsModel())
        runtime.activate("analyzer", {"type": "test", "payload": {}})
        self.assertEqual(runtime.calls, 3)
        self.assertEqual(len(runtime.queue), 1)
        messages = [e for e in self.events(runtime) if e["kind"] == "agent_message"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["message"]["recipient"], "planner")

    def test_planner_requests_remaining_operations_without_runtime_dispatch(self):
        class OneMoveModel(ScriptedDemoModel):
            def execute_moves(self, incoming):
                payload = incoming["payload"]
                single = dict(incoming, payload={**payload, "pending_operation_ids": payload["pending_operation_ids"][:1]})
                yield from super().execute_moves(single)

        create_fixture(self.root)
        runtime = Runtime(self.root, OneMoveModel(), apply=True, confirm=lambda plan: True,
                          max_calls=160, max_steps=100)
        state = runtime.run("organize all files")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(len(state["moves"]), 5)
        events = self.events(runtime)
        self.assertEqual(sum(e["kind"] == "execution_resumed" for e in events), 0)
        dispatches = [e["message"] for e in events if e["kind"] == "agent_message"
                      and e["message"]["type"] == "execution_request"]
        self.assertEqual(len(dispatches), 5)
        self.assertTrue(all(m["sender"] == "planner" and m["recipient"] == "executor" for m in dispatches))
        self.assertEqual(sum(e["kind"] == "confirmation" for e in events), 1)

    def test_executor_batches_keep_the_complete_plan_and_use_one_confirmation(self):
        create_fixture(self.root)
        runtime = Runtime(self.root, ScriptedDemoModel(), apply=True, confirm=lambda plan: True,
                          max_calls=160, max_steps=100, executor_batch_size=2)
        state = runtime.run("organize all files")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(len(state["moves"]), 5)
        events = self.events(runtime)
        dispatches = [e["message"]["payload"] for e in events if e["kind"] == "agent_message"
                      and e["message"]["sender"] == "planner" and e["message"]["type"] == "execution_request"]
        self.assertEqual([len(p["pending_operation_ids"]) for p in dispatches], [2, 2, 1])
        registered = [e["result"]["plan"] for e in events if e["kind"] == "tool_result" and e["tool"] == "get_plan"]
        self.assertTrue(all(len(p["operations"]) == 5 for p in registered))
        self.assertEqual(len({p["digest"] for p in dispatches}), 1)
        self.assertEqual(sum(e["kind"] == "confirmation" for e in events), 1)
        scheduled = [op for payload in dispatches for op in payload["pending_operation_ids"]]
        self.assertEqual(len(set(scheduled)), 5)
        self.assertEqual(len(scheduled), 5)

    def test_misspelled_source_is_rejected_as_invalid_proposal(self):
        create_fixture(self.root)
        runtime = Runtime(self.root, ScriptedDemoModel())
        result = runtime.submit_plan("typo", [{"source": "Lecture3.txt", "destination": "Courses/Lecture3.txt",
                                                "reason": "course", "evidence_refs": []}])
        self.assertEqual(result["error"], "invalid_source_paths")
        self.assertEqual(result["invalid_sources"], ["Lecture3.txt"])
        self.assertNotIn("current_files", result)
        self.assertEqual(runtime.plans, [])

    def test_review_feedback_is_not_execution_authorization(self):
        create_fixture(self.root)
        runtime = Runtime(self.root, ScriptedDemoModel(), apply=True,
                          confirm=lambda plan: "Keep exactly one representative per duplicate group.")
        result = runtime.submit_plan("review", [{"source": "lecture3.txt", "destination": "Courses/lecture3.txt",
                                                "reason": "course", "evidence_refs": []}])
        self.assertFalse(result["confirmed"])
        self.assertIn("review_feedback", result)
        self.assertFalse(runtime.queue)
        self.assertTrue((self.root / "lecture3.txt").exists())

    def test_reused_analysis_checks_hashes_and_records_original_evidence(self):
        create_fixture(self.root)
        (self.root / "unsupported.bin").unlink()
        source = Runtime(self.root, ScriptedDemoModel(), max_calls=100, max_steps=100)
        source.run("preview")
        resumed = Runtime(self.root, ScriptedDemoModel(), apply=True,
                          confirm=lambda plan: True, max_calls=160, max_steps=100)
        state = resumed.run("complete", analysis_from=source.trace.directory)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["analysis_provenance"]["source_run_id"], source.run_id)
        self.assertFalse(any(e.get("agent") == "analyzer" for e in self.events(resumed)))
        self.assertTrue(all(ref.startswith(source.run_id + ":")
                            for op in state["plans"][0]["operations"] for ref in op["evidence_refs"]))

    def test_reused_analysis_rejects_changed_bytes_even_when_size_matches(self):
        create_fixture(self.root)
        (self.root / "unsupported.bin").unlink()
        source = Runtime(self.root, ScriptedDemoModel(), max_calls=100, max_steps=100)
        source.run("preview")
        path = self.root / "lecture3.txt"
        path.write_bytes(b"x" * path.stat().st_size)
        resumed = Runtime(self.root, ScriptedDemoModel())
        with self.assertRaises(ValueError):
            resumed.run("retry", analysis_from=source.trace.directory)
        self.assertEqual(resumed.calls, 0)


if __name__ == "__main__":
    unittest.main()
