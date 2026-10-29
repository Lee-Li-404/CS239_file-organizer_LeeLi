import os
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from downloads_organizer.models import OpenAIModel, RequestPacer


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class RequestPacerTests(unittest.TestCase):
    def test_forty_requests_per_minute_without_a_startup_burst(self):
        clock = VirtualClock()
        pacer = RequestPacer(40, clock=clock, sleep=clock.sleep)
        starts = []
        for _ in range(41):
            pacer.wait()
            starts.append(clock())
        self.assertEqual(starts[0], 0)
        self.assertEqual(starts[39], 58.5)
        self.assertEqual(starts[40], 60)
        self.assertTrue(all(b-a >= 1.5 for a,b in zip(starts, starts[1:])))

    def test_slow_responses_consume_wait_and_idle_time_does_not_build_a_burst(self):
        clock = VirtualClock()
        pacer = RequestPacer(40, clock=clock, sleep=clock.sleep)
        pacer.wait()
        clock.now += 120
        pacer.wait()
        self.assertEqual(clock.waits, [])
        pacer.wait()
        self.assertEqual(clock.waits, [1.5])

    def test_early_wakeup_does_not_send_early(self):
        clock = VirtualClock()

        def interrupted_sleep(seconds):
            clock.waits.append(seconds)
            clock.now += seconds / 2 if len(clock.waits) == 1 else seconds

        pacer = RequestPacer(40, clock=clock, sleep=interrupted_sleep)
        pacer.wait()
        pacer.wait()
        self.assertEqual(clock(), 1.5)


class OpenAIModelTests(unittest.TestCase):
    def test_doubao_native_tool_round_trip_uses_ark_key_and_endpoint_without_network(self):
        import httpx
        from openai import DefaultHttpxClient
        from downloads_organizer.tools import agent_tools

        requests = []
        call = {"id": "ark_call_1", "type": "function", "function": {
            "name": "list_files", "arguments": "{}"}}

        def respond(request):
            requests.append(request)
            first = len(requests) == 1
            return httpx.Response(200, json={
                "id": "ark-test", "object": "chat.completion", "created": 0,
                "model": "doubao-seed-2-1-lite-260915", "choices": [{
                    "index": 0, "finish_reason": "tool_calls" if first else "stop",
                    "message": {"role": "assistant", "tool_calls": [call]} if first else
                               {"role": "assistant", "content": "Inventory received."}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}})

        clock = VirtualClock()
        transport = httpx.MockTransport(respond)
        with patch("openai.DefaultHttpxClient", side_effect=lambda **kw: DefaultHttpxClient(transport=transport, **kw)), \
                patch.dict(os.environ, {"LLM_PROVIDER": "doubao", "ARK_API_KEY": "ARK_TEST_ONLY",
                    "ARK_MODEL": "doubao-seed-2-1-lite-260915", "ARK_MAX_COMPLETION_TOKENS": "8000",
                    "OPENAI_API_KEY": "OPENAI_TEST_ONLY", "NVIDIA_API_KEY": "NV_TEST_ONLY", "LLM_MAX_RPM": "40"}):
            model = OpenAIModel()
            model._request_pacer = RequestPacer(40, clock=clock, sleep=clock.sleep)
            messages = [{"role": "user", "content": "Synthetic inventory request."}]
            tools = agent_tools("executor")
            result = model.complete("executor", messages, tools)
            self.assertEqual(result["message"]["tool_calls"], [call])
            self.assertEqual(result["usage"]["total_tokens"], 25)
            messages.extend([result["message"], {"role": "tool", "tool_call_id": call["id"],
                                                "content": '{"inventory":[]}'}])
            self.assertEqual(model.complete("executor", messages, tools)["message"]["content"], "Inventory received.")
            model.client.close()
        for request in requests:
            self.assertEqual(str(request.url), "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer ARK_TEST_ONLY")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "doubao-seed-2-1-lite-260915")
            self.assertEqual(body["max_completion_tokens"], 8000)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["tool_choice"], "auto")
            self.assertEqual(len(body["tools"]), 7)
            self.assertFalse(body["parallel_tool_calls"])
            self.assertFalse(body["stream"])
            for field in ["reasoning_effort", "max_tokens", "skip_special_tokens", "chat_template_kwargs"]:
                self.assertNotIn(field, body)
            self.assertNotIn("TEST_ONLY", request.content.decode())
        self.assertEqual(json.loads(requests[1].content)["messages"][-1]["tool_call_id"], "ark_call_1")
        self.assertEqual(clock.waits, [1.5])

    def test_doubao_missing_key_never_uses_other_provider_keys(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "doubao", "ARK_API_KEY": "",
                "OPENAI_API_KEY": "OPENAI_TEST_ONLY", "NVIDIA_API_KEY": "NV_TEST_ONLY"}), \
                patch("openai.OpenAI") as client:
            with self.assertRaisesRegex(ValueError, "Fill ARK_API_KEY"):
                OpenAIModel()
            client.assert_not_called()

    def test_doubao_env_file_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("LLM_PROVIDER=doubao\nARK_API_KEY=ARK_FILE_TEST_ONLY\n")
            with patch.dict(os.environ, {}, clear=True), patch("openai.OpenAI") as client:
                model = OpenAIModel(env)
                self.assertEqual(model.model, "doubao-seed-2-1-lite-260915")
                self.assertEqual(client.call_args.kwargs["base_url"], "https://ark.cn-beijing.volces.com/api/v3")
                self.assertEqual(client.call_args.kwargs["api_key"], "ARK_FILE_TEST_ONLY")
        for values, error in [({"ARK_MODEL": ""}, "ARK_MODEL"),
                              ({"ARK_MAX_COMPLETION_TOKENS": "262144"}, "ARK_MAX_COMPLETION_TOKENS")]:
            with self.subTest(values=values), patch.dict(os.environ, {"LLM_PROVIDER": "doubao",
                    "ARK_API_KEY": "ARK_TEST_ONLY", **values}), patch("openai.OpenAI") as client:
                with self.assertRaisesRegex(ValueError, error):
                    OpenAIModel()
                client.assert_not_called()

    def test_bolt_reasoning_can_be_enabled_and_invalid_values_fail_before_client_creation(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "nvidia", "NVIDIA_MODEL": "nvidia/nemotron-3.5-bolt-e4b",
                "NVIDIA_API_KEY": "NV_TEST_ONLY", "NVIDIA_ENABLE_THINKING": "true"}), \
                patch("openai.DefaultHttpxClient"), patch("openai.OpenAI") as client:
            message = SimpleNamespace(model_dump=lambda **kw: {"role": "assistant", "content": "Done."})
            client.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop", message=message)], usage=None)
            model = OpenAIModel()
            model.complete("analyzer", [], [])
            args = client.return_value.chat.completions.create.call_args.kwargs
            self.assertTrue(args["extra_body"]["chat_template_kwargs"]["enable_thinking"])
        with patch.dict(os.environ, {"LLM_PROVIDER": "nvidia", "NVIDIA_MODEL": "nvidia/nemotron-3.5-bolt-e4b",
                "NVIDIA_API_KEY": "NV_TEST_ONLY", "NVIDIA_ENABLE_THINKING": "maybe"}), patch("openai.OpenAI") as client:
            with self.assertRaisesRegex(ValueError, "NVIDIA_ENABLE_THINKING"):
                OpenAIModel()
            client.assert_not_called()

    def test_bolt_uses_documented_tool_parser_settings_for_both_aliases(self):
        for name in ["nvidia/nemotron-3.5-bolt-e4b", "stg/nvidia/nemotron-3.5-bolt-e4b"]:
            with self.subTest(model=name), patch.dict(os.environ, {"LLM_PROVIDER": "nvidia",
                    "NVIDIA_MODEL": name, "NVIDIA_API_KEY": "NV_TEST_ONLY"}), \
                    patch("openai.DefaultHttpxClient"), patch("openai.OpenAI") as client:
                message = SimpleNamespace(model_dump=lambda **kw: {"role": "assistant", "content": "Done."})
                client.return_value.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason="stop", message=message)], usage=None)
                OpenAIModel().complete("analyzer", [], [])
                args = client.return_value.chat.completions.create.call_args.kwargs
                self.assertEqual(args["model"], name)
                self.assertEqual(args["extra_body"], {"skip_special_tokens": False,
                                                    "chat_template_kwargs": {"enable_thinking": False}})
                self.assertFalse(args["stream"])

    def test_observed_nvidia_channel_suffix_is_repaired_with_an_audit_record(self):
        original = "upload_file<|channel|>commentary"
        assistant = {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
            "function": {"name": original, "arguments": '{"file":".anonymized/test.txt"}'}}]}
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls",
            message=SimpleNamespace(model_dump=lambda **kw: copy.deepcopy(assistant)))], usage=None)
        with patch.dict(os.environ, {"LLM_PROVIDER": "nvidia", "NVIDIA_MODEL": "openai/gpt-oss-20b",
                                    "NVIDIA_API_KEY": "NV_TEST_ONLY"}), \
                patch("openai.DefaultHttpxClient"), patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            result = OpenAIModel().complete("analyzer", [], [{"function": {"name": "upload_file"}}])
        call = result["message"]["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["function"]["name"], "upload_file")
        self.assertEqual(call["function"]["arguments"], assistant["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(result["tool_name_repairs"][0]["original"], original)
        self.assertEqual(result["tool_name_repairs"][0]["normalized"], "upload_file")

    def test_channel_suffix_repair_cannot_grant_an_unregistered_tool_or_change_other_models(self):
        cases = [
            ("nvidia", "openai/gpt-oss-20b", "run_command<|channel|>commentary", "send_message"),
            ("nvidia", "openai/gpt-oss-20b", "upload_file<|channel|>analysis", "upload_file"),
            ("nvidia", "openai/gpt-oss-20b", "upload_file<|channel|>commentary junk", "upload_file"),
            ("nvidia", "deepseek-ai/deepseek-v4.1-flash", "upload_file<|channel|>commentary", "upload_file"),
            ("openai", "openai/gpt-oss-20b", "upload_file<|channel|>commentary", "upload_file"),
        ]
        for provider, model_name, returned_name, registered_name in cases:
            with self.subTest(provider=provider, model=model_name, name=returned_name):
                assistant = {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                    "function": {"name": returned_name, "arguments": "{}"}}]}
                response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls",
                    message=SimpleNamespace(model_dump=lambda **kw: copy.deepcopy(assistant)))], usage=None)
                with patch.dict(os.environ, {"LLM_PROVIDER": provider, "NVIDIA_MODEL": model_name,
                        "OPENAI_MODEL": model_name, "NVIDIA_API_KEY": "NV_TEST_ONLY", "OPENAI_API_KEY": "OA_TEST_ONLY"}), \
                        patch("openai.DefaultHttpxClient"), patch("openai.OpenAI") as client:
                    client.return_value.chat.completions.create.return_value = response
                    result = OpenAIModel().complete("planner", [], [{"function": {"name": registered_name}}])
                self.assertEqual(result["message"], assistant)
                self.assertNotIn("tool_name_repairs", result)

    def test_chat_tool_protocol_is_preserved_without_network(self):
        assistant = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {
                "name": "anonymize_file", "arguments": '{"path":"notes.txt"}'}}]}
        response_message = MagicMock()
        response_message.model_dump.return_value = assistant
        response = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="tool_calls", message=response_message)],
            usage=None)
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "FAKE_TEST_ONLY", "OPENAI_MODEL": "test-model"}), \
                patch("openai.OpenAI") as client_class:
            client_class.return_value.chat.completions.create.return_value = response
            model = OpenAIModel()
            result = model.complete("analyzer", [{"role": "user", "content": "test"}], [])
            self.assertEqual(result["message"], assistant)
            arguments = client_class.return_value.chat.completions.create.call_args.kwargs
            self.assertEqual(arguments["model"], "test-model")
            self.assertNotIn("FAKE_TEST_ONLY", str(arguments))
            self.assertFalse(arguments["parallel_tool_calls"])

    def test_missing_key_fails_before_client_creation(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": ""}), patch("openai.OpenAI") as client:
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                OpenAIModel()
            client.assert_not_called()

    def test_nvidia_tool_round_trip_uses_selected_key_and_endpoint(self):
        import httpx
        from openai import DefaultHttpxClient

        requests = []
        call = {"id": "call_1", "type": "function", "function": {
            "name": "anonymize_file", "arguments": '{"path":"notes.txt"}'}}

        def respond(request):
            requests.append(request)
            message = ({"role": "assistant", "content": None, "tool_calls": [call]}
                       if len(requests) == 1 else {"role": "assistant", "content": "Done."})
            return httpx.Response(200, json={
                "id": "test-completion", "object": "chat.completion", "created": 0,
                "model": "deepseek-ai/deepseek-v4.1-flash", "choices": [{
                    "index": 0, "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                    "message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

        transport = httpx.MockTransport(respond)
        clock = VirtualClock()
        with patch("openai.DefaultHttpxClient", side_effect=lambda **kw: DefaultHttpxClient(transport=transport, **kw)), \
                patch.dict(os.environ, {"LLM_PROVIDER": "nvidia", "NVIDIA_API_KEY": "NV_TEST_ONLY",
                    "NVIDIA_MODEL": "deepseek-ai/deepseek-v4.1-flash", "NVIDIA_MAX_TOKENS": "8000",
                    "OPENAI_API_KEY": "OPENAI_TEST_ONLY", "OPENAI_MAX_COMPLETION_TOKENS": "512"}):
            model = OpenAIModel()
            model._request_pacer = RequestPacer(40, clock=clock, sleep=clock.sleep)
            messages = [{"role": "user", "content": "Synthetic notes only."}]
            tools = [{"type": "function", "function": {"name": "anonymize_file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
            result = model.complete("analyzer", messages, tools)
            self.assertEqual(result["message"]["tool_calls"], [call])
            self.assertEqual(result["usage"]["total_tokens"], 15)
            messages.extend([result["message"], {"role": "tool", "tool_call_id": "call_1",
                "content": '{"output_file":".anonymized/example.txt"}'}])
            self.assertEqual(model.complete("analyzer", messages, tools)["message"]["content"], "Done.")
            model.client.close()

        for request in requests:
            self.assertEqual(str(request.url), "https://integrate.api.nvidia.com/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer NV_TEST_ONLY")
            body = json.loads(request.content)
            self.assertEqual(body["max_tokens"], 8000)
            self.assertEqual(body["tool_choice"], "auto")
            self.assertFalse(body["parallel_tool_calls"])
            self.assertNotIn("max_completion_tokens", body)
            self.assertNotIn("reasoning_effort", body)
            self.assertNotIn("TEST_ONLY", request.content.decode())
        self.assertEqual(json.loads(requests[1].content)["messages"][-1]["tool_call_id"], "call_1")

    def test_missing_nvidia_key_never_falls_back_to_openai(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "nvidia", "NVIDIA_API_KEY": "",
                                    "OPENAI_API_KEY": "OPENAI_TEST_ONLY"}), patch("openai.OpenAI") as client:
            with self.assertRaisesRegex(ValueError, "NVIDIA_API_KEY"):
                OpenAIModel()
            client.assert_not_called()

    def test_http_retries_and_all_agent_roles_share_the_same_rate_limit(self):
        import httpx
        from openai import DefaultHttpxClient

        for provider, first_failure in [(p, failure) for p in ["nvidia", "doubao"]
                                        for failure in ["429", "timeout"]]:
            with self.subTest(provider=provider, first_failure=first_failure):
                clock = VirtualClock()
                starts = []

                def respond(request):
                    starts.append(clock())
                    if len(starts) == 1:
                        if first_failure == "timeout":
                            raise httpx.ReadTimeout("synthetic test timeout", request=request)
                        return httpx.Response(429, headers={"retry-after": "0.001"},
                                              json={"error": {"message": "synthetic rate limit"}})
                    return httpx.Response(200, json={
                        "id": "test-completion", "object": "chat.completion", "created": 0,
                        "model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "Done."}}]})

                transport = httpx.MockTransport(respond)
                with patch("openai.DefaultHttpxClient", side_effect=lambda **kw: DefaultHttpxClient(transport=transport, **kw)), \
                        patch.dict(os.environ, {"LLM_PROVIDER": provider, "LLM_MAX_RPM": "40",
                                                "NVIDIA_API_KEY": "NV_TEST_ONLY", "ARK_API_KEY": "ARK_TEST_ONLY"}):
                    model = OpenAIModel()
                    model._request_pacer = RequestPacer(model.max_rpm, clock=clock, sleep=clock.sleep)
                    for role in ["analyzer", "planner", "executor"]:
                        model.complete(role, [{"role": "user", "content": "synthetic"}], [])
                    model.client.close()
                self.assertEqual(starts, [0, 1.5, 3, 4.5])

    def test_invalid_rpm_is_rejected_before_client_creation(self):
        for limit in ["0", "-1", "forty", "1.5"]:
            with self.subTest(limit=limit), \
                    patch.dict(os.environ, {"LLM_PROVIDER": "nvidia", "NVIDIA_API_KEY": "NV_TEST_ONLY",
                                            "LLM_MAX_RPM": limit}), \
                    patch("openai.OpenAI") as client:
                with self.assertRaisesRegex(ValueError, "LLM_MAX_RPM"):
                    OpenAIModel()
                client.assert_not_called()

    def test_openai_has_no_local_pacing_and_retains_sdk_retries(self):
        import httpx
        from openai import DefaultHttpxClient

        requests = []

        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(429, headers={"retry-after": "0.001"},
                                      json={"error": {"message": "synthetic rate limit"}})
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 0,
                "model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Done."}}]})

        transport = httpx.MockTransport(respond)
        with patch("openai.DefaultHttpxClient", side_effect=lambda **kw: DefaultHttpxClient(transport=transport, **kw)), \
                patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "TEST_ONLY",
                                        "LLM_MAX_RPM": "ignored-for-openai"}):
            model = OpenAIModel()
            self.addCleanup(model.client.close)
            self.assertIsNone(model.max_rpm)
            self.assertIsNone(model._request_pacer)
            self.assertEqual(model.client._client.event_hooks["request"], [])
            for role in ("interpreter", "planner", "executor"):
                self.assertEqual(model.complete(role, [], [])["message"]["content"], "Done.")
        self.assertEqual(len(requests), 4)

    def test_invalid_provider_and_nvidia_token_budget_fail_before_client_creation(self):
        for values, error in [({"LLM_PROVIDER": "typo"}, "LLM_PROVIDER"),
                              ({"LLM_PROVIDER": "nvidia", "NVIDIA_API_KEY": "NV_TEST_ONLY",
                                "NVIDIA_MAX_TOKENS": "262144"}, "NVIDIA_MAX_TOKENS")]:
            with self.subTest(values=values), patch.dict(os.environ, values), patch("openai.OpenAI") as client:
                with self.assertRaisesRegex(ValueError, error):
                    OpenAIModel()
                client.assert_not_called()

    def test_env_file_selects_nvidia_without_overriding_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("LLM_PROVIDER=nvidia\nNVIDIA_API_KEY=FILE_TEST_ONLY\nNVIDIA_MAX_TOKENS=4000\n")
            with patch.dict(os.environ, {}, clear=True), patch("openai.OpenAI") as client:
                self.assertEqual(OpenAIModel(env).provider, "nvidia")
                self.assertEqual(client.call_args.kwargs["api_key"], "FILE_TEST_ONLY")
            with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "ENV_TEST_ONLY"}, clear=True), \
                    patch("openai.OpenAI") as client:
                self.assertEqual(OpenAIModel(env).provider, "openai")
                self.assertEqual(client.call_args.kwargs["api_key"], "ENV_TEST_ONLY")


if __name__ == "__main__":
    unittest.main()
