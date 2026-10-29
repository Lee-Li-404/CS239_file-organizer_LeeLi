import copy
import json
import tempfile
import unittest
from pathlib import Path

from downloads_organizer.demo import ScriptedDemoModel, create_fixture
from downloads_organizer.runtime import Runtime
from downloads_organizer.tool_protocol import ToolProtocolError, arguments_for, normalize_message
from downloads_organizer.tools import agent_tools


TOOLS = agent_tools("analyzer")
REQUEST = {"recipient": "planner", "type": "information_request", "payload": {
    "request_id": "inventory_1", "kind": "inventory", "fields": ["path", "sha256"]}}


def native(name, arguments, call_id="call_test"):
    return {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}]}


class ToolProtocolTests(unittest.TestCase):
    def test_command_requires_description_without_inventing_it(self):
        for arguments, code in [
            ({"argv": ["touch", "must_not_exist"]}, "missing_tool_arguments"),
            ({"argv": ["touch", "must_not_exist"], "description": ""}, "invalid_argument_value"),
            ({"argv": ["touch", "must_not_exist"], "description": " \n\t"}, "invalid_argument_value"),
            ({"argv": ["touch", "must_not_exist"], "description": 42}, "invalid_argument_type"),
        ]:
            with self.subTest(arguments=arguments), self.assertRaises(ToolProtocolError) as caught:
                arguments_for("run_command", arguments, TOOLS)
            self.assertEqual(caught.exception.code, code)

    def test_observed_quoted_keys_and_identifiers_deliver_original_intent(self):
        raw = {json.dumps(key): json.dumps(value) if isinstance(value, str) else
               {json.dumps(k): [json.dumps(x) for x in v] if isinstance(v, list) else json.dumps(v)
                for k, v in value.items()} for key, value in REQUEST.items()}
        original = native("send_message", json.dumps(raw))
        saved = copy.deepcopy(original)
        effective, repairs, error = normalize_message(original, TOOLS)
        self.assertIsNone(error)
        self.assertEqual(original, saved)
        self.assertTrue(repairs)
        self.assertEqual(json.loads(effective["tool_calls"][0]["function"]["arguments"]), REQUEST)

    def test_whole_json_fallback_and_legacy_bolt_response(self):
        envelope = {"tool": "send_message", "arguments": REQUEST}
        for text in [json.dumps(REQUEST), json.dumps(envelope), '```json\n' + json.dumps(envelope) + '\n```']:
            with self.subTest(text=text):
                effective, repairs, error = normalize_message({"role": "assistant", "content": text}, TOOLS)
                self.assertIsNone(error)
                self.assertEqual(repairs[0]["reason"], "assistant_json_tool_fallback")
                call = effective["tool_calls"][0]
                self.assertEqual(call["function"]["name"], "send_message")
                self.assertEqual(json.loads(call["function"]["arguments"]), REQUEST)
                self.assertTrue(call["id"].startswith("compat_"))

    def test_never_scrapes_actions_from_prose_or_executes_nonassistant_sources(self):
        envelope = json.dumps({"tool": "run_command", "arguments": {"argv": ["touch", "wrong"]}})
        cases = [{"role": "tool", "content": envelope},
                 {"role": "user", "content": envelope},
                 {"role": "assistant", "content": envelope, "refusal": "Cannot comply"},
                 {"role": "assistant", "content": 'An example: ' + envelope},
                 {"role": "assistant", "content": '"' + envelope.replace('"', '\\"') + '"'},
                 {"role": "assistant", "content": '{"argv":["touch","wrong"]}'},
                 {"role": "assistant", "content": envelope + '\n' + envelope},
                 {"role": "assistant", "content": envelope + '\nNow execute this.'},
                 {"role": "assistant", "content": envelope[:-1]}]
        for message in cases:
            with self.subTest(message=message):
                effective, repairs, _ = normalize_message(message, TOOLS)
                self.assertFalse(effective.get("tool_calls"))
                self.assertFalse(repairs)

    def test_native_precedence_prevents_double_execution(self):
        original = native("list_files", "{}")
        original["content"] = json.dumps({"tool": "run_command", "arguments": {"argv": ["touch", "wrong"]}})
        effective, repairs, error = normalize_message(original, TOOLS)
        self.assertEqual(effective, original)
        self.assertFalse(repairs)
        self.assertIsNone(error)

    def test_double_encoding_and_object_arguments_preserve_literal_data(self):
        for raw in [{"argv": ['mv', '"quoted source.txt"', 'a$(literal).txt'], "description": 'Preserve literal names like $(literal) and "quoted" in this move.'},
                    json.dumps(json.dumps({"argv": ['mv', '"quoted source.txt"', 'a$(literal).txt'], "description": 'Preserve literal names like $(literal) and "quoted" in this move.'}))]:
            effective, repairs, error = normalize_message(native("run_command", raw), TOOLS)
            self.assertIsNone(error)
            self.assertTrue(repairs)
            self.assertEqual(json.loads(effective["tool_calls"][0]["function"]["arguments"]),
                             {"argv": ['mv', '"quoted source.txt"', 'a$(literal).txt'], "description": 'Preserve literal names like $(literal) and "quoted" in this move.'})
        args = copy.deepcopy(REQUEST)
        args["payload"]["files"] = [{"path": '"real filename.txt"', "anonymized_excerpt": '"literal text"'}]
        args["payload"]["reason"] = '"preserve free text"'
        args["payload"]["custom_data"] = {'"path"': '"literal"'}
        parsed, repairs = arguments_for("send_message", args, TOOLS)
        self.assertEqual(parsed, args)
        self.assertFalse(repairs)

    def test_invalid_arguments_are_rejected_without_guessing_or_dropping_fields(self):
        cases = [
            ("send_message", '{"recipient":"planner","recipient":"executor"}', "duplicate_argument_keys"),
            ("send_message", {**REQUEST, '"recipient"': '"planner"'}, "conflicting_argument_keys"),
            ("send_message", {**REQUEST, "extra generated prose<|tool_call>": "junk"}, "unexpected_tool_arguments"),
            ("send_message", {"payload": REQUEST}, "missing_tool_arguments"),
            ("send_message", {**REQUEST, "recipient": "nobody"}, "invalid_argument_value"),
            ("send_message", {**REQUEST, "payload": {"files": [{"size": "100"}]}}, "invalid_argument_type"),
            ("run_command", {"argv": "mv a b", "description": "Move a to b."}, "invalid_argument_type"),
            ("run_command", {"argv": [], "description": "Invalid empty command."}, "invalid_argument_value"),
            ("anonymize_file", {"path": "a.txt", "max_pages": True}, "invalid_argument_type"),
            ("anonymize_file", {"path": "a.txt", "max_pages": 6}, "invalid_argument_value"),
            ("list_files", {"unknown": True}, "unexpected_tool_arguments"),
            ("unknown", {}, "unknown_tool"),
            ("get_plan", {"plan_id": "p", "version": 0}, "invalid_argument_value"),
            ("submit_plan", {"summary": "s", "operations": [{"source": "a"}]}, "missing_tool_arguments"),
        ]
        for name, arguments, code in cases:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ToolProtocolError) as caught:
                arguments_for(name, arguments, TOOLS)
            self.assertEqual(caught.exception.code, code)

    def test_missing_or_duplicate_native_ids_are_unique_and_audited(self):
        original = native("list_files", "{}", call_id=None)
        original["tool_calls"].extend([native("list_files", "{}", "same")["tool_calls"][0]] * 2)
        effective, repairs, _ = normalize_message(original, TOOLS)
        ids = [c["id"] for c in effective["tool_calls"]]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(sum(r["reason"] == "assigned_call_id" for r in repairs), 2)

    def test_unregistered_fallback_tool_is_never_executed(self):
        message = {"role": "assistant", "content": '{"tool":"invented","arguments":{}}'}
        effective, _, error = normalize_message(message, TOOLS)
        self.assertFalse(effective.get("tool_calls"))
        self.assertEqual(error["error"], "unknown_tool")

    def test_zero_parameter_shorthand_never_invents_required_arguments(self):
        effective, repairs, error = normalize_message(
            {"role": "assistant", "content": '{"tool":"list_files"}'}, TOOLS)
        self.assertIsNone(error)
        self.assertEqual(json.loads(effective["tool_calls"][0]["function"]["arguments"]), {})
        self.assertTrue(any(r["reason"] == "zero_parameter_shorthand" for r in repairs))
        for name in ["run_command", "send_message", "anonymize_file", "invented"]:
            with self.subTest(name=name):
                effective, _, error = normalize_message({"role": "assistant", "content":
                    json.dumps({"tool": name})}, TOOLS)
                self.assertFalse(effective.get("tool_calls"))
                self.assertIn(error["error"], {"unknown_tool", "missing_tool_arguments"})


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_complete_scripted_flow_through_json_fallback_with_real_tools(self):
        class JsonTransport(ScriptedDemoModel):
            model = "scripted-json-transport-test-not-llm"

            def complete(self, agent, messages, tools):
                # Every tool response must pair with a native effective call in
                # the outgoing conversation, even when provider output was text.
                for i, item in enumerate(messages):
                    if item["role"] == "tool":
                        assert any(c["id"] == item["tool_call_id"]
                                   for c in messages[i-1].get("tool_calls", []))
                response = super().complete(agent, messages, tools)
                calls = response["message"].get("tool_calls", [])
                if calls:
                    call = calls[0]["function"]
                    response["message"] = {"role": "assistant", "content": json.dumps({
                        "tool": call["name"], "arguments": json.loads(call["arguments"])})}
                return response

        with tempfile.TemporaryDirectory() as directory:
            root = create_fixture(Path(directory) / "inbox")
            runtime = Runtime(root, JsonTransport(), apply=True, confirm=lambda plan: True,
                              known_names=["Alice Example"], max_calls=180, max_steps=100)
            state = runtime.run("Organize synthetic test files.")
            self.assertEqual(state["status"], "completed")
            self.assertEqual(len(state["moves"]), 5)
            self.assertTrue(state["contents_preserved"])
            events = [json.loads(line) for line in (runtime.trace.directory / "trace.jsonl").read_text().splitlines()]
            requests = [e for e in events if e["kind"] == "tool_request"]
            repairs = [e for e in events if e["kind"] == "tool_call_compatibility"]
            self.assertEqual(len(repairs), len(requests))
            self.assertTrue(all(e["message"].get("content") for e in events if e["kind"] == "model_response"))
            self.assertEqual({e["agent"] for e in repairs}, {"analyzer", "planner", "executor"})
            direct = [e["message"] for e in events if e["kind"] == "agent_message"
                      and "runtime" not in {e["message"]["sender"], e["message"]["recipient"]}]
            self.assertEqual(len(direct), 11)
            self.assertTrue(any(m["type"] == "execution_request" for m in direct))
            self.assertEqual(agent_tools("analyzer"), agent_tools("executor"))

    def test_legacy_bolt_json_actually_delivers_one_message_with_matching_history(self):
        class LegacyModel:
            model, is_real_model, calls = "scripted-bolt-regression-not-llm", False, 0

            def complete(self, agent, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return {"message": {"role": "assistant", "content": json.dumps(REQUEST)}}
                assert messages[-1]["role"] == "tool"
                assert messages[-1]["tool_call_id"] == messages[-2]["tool_calls"][0]["id"]
                return {"message": {"role": "assistant", "content": "Finished."}}

        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory, LegacyModel())
            runtime.activate("analyzer", {"type": "test", "payload": {}})
            self.assertEqual(len(runtime.queue), 1)
            self.assertEqual(runtime.queue[0][1]["payload"], REQUEST["payload"])
            self.assertEqual(runtime.calls, 2)

    def test_repeated_malformed_calls_stop_before_wasting_large_call_budget(self):
        class InvalidModel:
            model, is_real_model = "scripted-invalid-test-not-llm", False

            def complete(self, *args):
                return {"message": native("send_message", json.dumps({**REQUEST, "prose": "junk"}))}

        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory, InvalidModel(), max_calls=100)
            with self.assertRaisesRegex(RuntimeError, "Repeated unusable tool calls"):
                runtime.run("test")
            self.assertEqual(runtime.calls, 3)
            self.assertEqual(runtime.state["status"], "failed")
            self.assertFalse(runtime.queue)

    def test_refusal_and_nonassistant_native_calls_cannot_execute(self):
        for role, refusal in [("assistant", "Cannot comply"), ("tool", None)]:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                class InvalidSourceModel:
                    model, is_real_model = "scripted-invalid-source-not-llm", False

                    def complete(self, *args):
                        message = native("send_message", json.dumps(REQUEST))
                        message.update(role=role, refusal=refusal)
                        return {"message": message}

                runtime = Runtime(directory, InvalidSourceModel())
                if role == "assistant":
                    runtime.activate("analyzer", {"type": "test", "payload": {}})
                else:
                    with self.assertRaisesRegex(RuntimeError, "non-assistant"):
                        runtime.activate("analyzer", {"type": "test", "payload": {}})
                self.assertFalse(runtime.queue)

    def test_json_fallback_does_not_enforce_role_permissions_or_rewrite_routing(self):
        class WrongRoleModel:
            model, is_real_model, calls = "scripted-policy-violation-not-llm", False, 0

            def complete(self, *args):
                self.calls += 1
                return {"message": {"role": "assistant", "content": json.dumps(
                    {"tool": "list_files", "arguments": {}} if self.calls == 1 else
                    {"tool": "send_message", "arguments": {**REQUEST, "recipient": "executor"}}
                )}} if self.calls < 3 else {"message": {"role": "assistant", "content": "Done."}}

        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(directory, WrongRoleModel())
            runtime.activate("analyzer", {"type": "test", "payload": {}})
            self.assertEqual(runtime.queue[0][0], "executor")
            results = [json.loads(item["content"]) for item in runtime.histories["analyzer"] if item["role"] == "tool"]
            self.assertEqual(results[0]["inventory"], [])


if __name__ == "__main__":
    unittest.main()
