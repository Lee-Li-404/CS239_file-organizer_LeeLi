"""Real model adapter. No credentials are placed in model messages or traces."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path


class RequestPacer:
    """Space request starts evenly, sharing one budget across roles and retries."""

    def __init__(self, max_rpm: int, *, clock=None, sleep=None):
        if max_rpm < 1:
            raise ValueError("LLM_MAX_RPM must be a positive integer.")
        self.interval = 60.0 / max_rpm
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._next_start = None
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            if self._next_start is not None:
                delay = self._next_start - self._clock()
                while delay > 0:
                    self._sleep(delay)
                    delay = self._next_start - self._clock()
            self._next_start = self._clock() + self.interval


class OpenAIModel:
    """OpenAI-compatible adapter; LLM_PROVIDER selects credentials and endpoint.

    The historical class name is retained for existing callers. There is no
    automatic provider fallback: changing the recipient must be deliberate.
    """

    is_real_model = True

    def __init__(self, env_file: str | Path | None = None):
        from dotenv import load_dotenv
        from openai import DefaultHttpxClient, OpenAI

        if env_file is not None:
            load_dotenv(env_file, override=False)
        self.provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
        if self.provider not in {"openai", "nvidia", "doubao"}:
            raise ValueError("LLM_PROVIDER must be openai, nvidia or doubao.")
        prefix = "ARK" if self.provider == "doubao" else self.provider.upper()
        key_name = f"{prefix}_API_KEY"
        key = os.environ.get(key_name, "").strip()
        if not key:
            raise ValueError(f"Fill {key_name} in the project .env before a real run.")
        default_model = {"openai": "gpt-5.6-luna",
                         "nvidia": "deepseek-ai/deepseek-v4.1-flash",
                         "doubao": "doubao-seed-2-1-lite-260915"}[self.provider]
        self.model = os.environ.get(f"{prefix}_MODEL", default_model).strip()
        if not self.model:
            raise ValueError(f"{prefix}_MODEL must not be empty.")
        self.enable_thinking = None
        if self.provider == "nvidia" and self.model.removeprefix("stg/") == "nvidia/nemotron-3.5-bolt-e4b":
            thinking = os.environ.get("NVIDIA_ENABLE_THINKING", "false").strip().lower()
            if thinking not in {"true", "false"}:
                raise ValueError("NVIDIA_ENABLE_THINKING must be true or false.")
            self.enable_thinking = thinking == "true"
        limit_name = ("NVIDIA_MAX_TOKENS" if self.provider == "nvidia"
                      else f"{prefix}_MAX_COMPLETION_TOKENS")
        self.max_completion_tokens = int(os.environ.get(limit_name, "4000"))
        if not 512 <= self.max_completion_tokens <= 16000:
            raise ValueError(f"{limit_name} must be between 512 and 16000.")
        self.max_rpm = None
        self._request_pacer = None
        hooks = {}
        if self.provider != "openai":
            try:
                self.max_rpm = int(os.environ.get("LLM_MAX_RPM", "40"))
            except ValueError:
                raise ValueError("LLM_MAX_RPM must be a positive integer.") from None
            self._request_pacer = RequestPacer(self.max_rpm)
            # Non-OpenAI providers share pacing across roles and SDK retries.
            hooks["request"] = [self._pace_request]
        http_client = DefaultHttpxClient(event_hooks=hooks)
        endpoints = {"nvidia": "https://integrate.api.nvidia.com/v1",
                     "doubao": "https://ark.cn-beijing.volces.com/api/v3"}
        client_options = {"base_url": endpoints[self.provider]} if self.provider in endpoints else {}
        self.client = OpenAI(api_key=key, timeout=60 if self.provider == "openai" else 120,
                             max_retries=1, http_client=http_client, **client_options)

    def _pace_request(self, request):
        if self._request_pacer is not None:
            self._request_pacer.wait()

    def complete(self, agent: str, messages: list, tools: list) -> dict:
        # The inexpensive default supports reasoning=none. Avoid its default
        # medium reasoning overhead for simple classification and tool routing.
        if self.provider == "nvidia":
            options = {"max_tokens": self.max_completion_tokens,
                       "temperature": 1, "top_p": 0.95, "stream": False}
            if self.model.removeprefix("stg/") == "nvidia/nemotron-3.5-bolt-e4b":
                # NVIDIA's Bolt API documents these settings for tool parsing.
                options["extra_body"] = {"skip_special_tokens": False,
                                         "chat_template_kwargs": {"enable_thinking": self.enable_thinking}}
        elif self.provider == "doubao":
            # Ark supports the Chat Completions function-call protocol. Keep
            # thinking disabled: enabled Seed 2.1 calls require opaque reasoning
            # state to be replayed, which this adapter does not currently retain.
            options = {"max_completion_tokens": self.max_completion_tokens,
                       "stream": False, "extra_body": {"thinking": {"type": "disabled"}}}
        else:
            options = {"max_completion_tokens": self.max_completion_tokens}
            if self.model == "gpt-5.6-luna":
                options["reasoning_effort"] = "none"
        if tools:
            options["tool_choice"] = "auto"
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            parallel_tool_calls=False,
            **options,
        )
        if not response.choices or response.choices[0].finish_reason in {"length", "content_filter"}:
            raise RuntimeError("Model did not return a complete usable response.")
        message = response.choices[0].message.model_dump(exclude_none=True)
        # Keep the Chat Completions fields needed by the next request.
        message = {k: v for k, v in message.items()
                   if k in {"role", "content", "tool_calls", "refusal"}}
        repairs = []
        # Observed NVIDIA gpt-oss-20b responses can append a Harmony channel
        # marker to the function name. Repair only this exact suffix and only
        # if the resulting name is in THIS role's supplied tool definitions.
        # Keep the original names in the trace; arguments and call IDs stay intact.
        if self.provider == "nvidia" and self.model == "openai/gpt-oss-20b":
            registered = {tool["function"]["name"] for tool in tools}
            suffix = "<|channel|>commentary"
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                original = function.get("name", "")
                name = original.removesuffix(suffix)
                if name != original and name in registered:
                    function["name"] = name
                    repairs.append({"call_id": call["id"], "original": original,
                                    "normalized": name, "reason": "nvidia_gpt_oss_channel_suffix"})
        result = {"message": message,
                  "usage": response.usage.model_dump() if response.usage else None}
        if repairs:
            result["tool_name_repairs"] = repairs
        return result
