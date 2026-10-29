"""Run a fresh real-model fixture and estimate cost from returned API usage.

Run as: python3 -m downloads_organizer.live_test
An authorized operator still reviews each plan; this module does not approve it.
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict
from pathlib import Path

from .cli import DEFAULT_TASK, confirm_plan
from .demo import create_fixture
from .models import OpenAIModel
from .runtime import Runtime


# Standard short-context text rates checked 2026-10-28. Estimates, not invoices.
# This adapter uses the default cache retention, not explicit extended retention.
RATES = {"gpt-5.6-luna": {"input": 0.20, "cached_input": 0.02,
                          "cache_write": 0.25, "output": 1.20}}


def usage_report(trace_path: Path, model: str, provider: str = "openai") -> dict:
    totals = defaultdict(int, {key: 0 for key in (
        "requests", "responses_with_usage", "input_tokens", "cached_input_tokens",
        "output_tokens", "reasoning_tokens", "cache_write_tokens")})
    actors = defaultdict(lambda: defaultdict(int))
    for line in trace_path.read_text().splitlines():
        event = json.loads(line)
        if event["kind"] == "model_request":
            totals["requests"] += 1
        if event["kind"] != "model_response" or not event.get("usage"):
            continue
        usage = event["usage"]
        values = {
            "responses_with_usage": 1,
            "input_tokens": usage.get("prompt_tokens", 0),
            "cached_input_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            "cache_write_tokens": (usage.get("prompt_tokens_details") or {}).get("cache_write_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
        }
        for key, value in values.items():
            totals[key] += value or 0
            actors[event["agent"]][key] += value or 0
    totals["uncached_input_tokens"] = totals["input_tokens"] - totals["cached_input_tokens"]
    totals["ordinary_input_tokens"] = totals["uncached_input_tokens"] - totals["cache_write_tokens"]
    if totals["ordinary_input_tokens"] < 0:
        raise ValueError("Cached input plus cache writes exceeds total input tokens.")
    report = {"model": model, "provider": provider, "usage": dict(totals),
              "by_agent": {key: dict(value) for key, value in actors.items()},
              "note": "Estimate from recorded API usage, not a billing invoice; excludes taxes and any unreported charges."}
    rate = RATES.get(model) if provider == "openai" else None
    if rate:
        report["pricing_checked"] = "2026-10-28"
        report["source"] = "https://developers.openai.com/api/docs/models/gpt-5.6-luna"
        report["rates_usd_per_million"] = rate
        report["estimated_usd"] = round((totals["ordinary_input_tokens"] * rate["input"]
                                        + totals["cache_write_tokens"] * rate["cache_write"]
                                        + totals["cached_input_tokens"] * rate["cached_input"]
                                        + totals["output_tokens"] * rate["output"]) / 1_000_000, 8)
        report["estimated_cache_write_premium_usd"] = round(
            totals["cache_write_tokens"] * (rate["cache_write"] - rate["input"]) / 1_000_000, 8)
        report["cache_write_accounting"] = (
            "Reported cache_write_tokens are a subset of uncached input, charged once at the default "
            "cache-write rate. Cached reads are separate; reasoning is included in output.")
        report["estimated_usd_without_input_cache"] = round((totals["input_tokens"] * rate["input"]
                                                            + totals["output_tokens"] * rate["output"])
                                                           / 1_000_000, 8)
    else:
        report["estimated_usd"] = None
        report["note"] = ("Token usage only; provider billing or trial eligibility has not been verified. "
                          "An unknown cost is not zero.")
    report["usage_complete"] = totals["requests"] == totals["responses_with_usage"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["normal", "conflict", "injection", "hard", "deletion_clean", "deletion_attack"], default="normal")
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    parser.add_argument("--max-calls", type=int, default=80)
    parser.add_argument("--max-steps", type=int, default=64)
    parser.add_argument("--executor-batch-size", type=int,
                        help="Pending operations per Executor activation; full plan/confirmation stay unchanged")
    parser.add_argument("--workspace", type=Path, help="Use an existing test copy instead of synthetic fixtures")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--names-file", type=Path, help="Local name list, never sent to the model")
    parser.add_argument("--analysis-from", type=Path,
                        help="Explicit recovery: reuse a recorded Analyzer report after verifying every source hash")
    args = parser.parse_args()
    if args.scenario == "hard" and args.task == DEFAULT_TASK:
        from .scenarios import HARD_TASK
        args.task = HARD_TASK
    elif args.scenario in {"deletion_clean", "deletion_attack"} and args.task == DEFAULT_TASK:
        from .scenarios import DELETION_TASK
        args.task = DELETION_TASK
    model = OpenAIModel(args.env_file)
    workspace = args.workspace or create_fixture(
        Path("demo/workspaces") / ("live-" + args.scenario + "-" + uuid.uuid4().hex[:8]), args.scenario)
    names = args.names_file.read_text(encoding="utf-8").splitlines() if args.names_file else []
    runtime = Runtime(workspace, model, apply=True, confirm=confirm_plan,
                      max_calls=args.max_calls, max_steps=args.max_steps, known_names=names,
                      executor_batch_size=args.executor_batch_size,
                      progress=lambda value: print(value, flush=True),
                      confirmation_source="authorized_test_operator")
    print("LIVE MODEL:", model.provider, model.model, flush=True)
    print("RUN DIRECTORY:", runtime.trace.directory, flush=True)
    failed = False
    try:
        runtime.run(args.task, analysis_from=args.analysis_from)
    except Exception as error:
        failed = True
        # Print only classification fields; provider exception strings can contain data.
        print("ERROR:", type(error).__name__, "HTTP:", getattr(error, "status_code", None), flush=True)
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            detail = body.get("error", body)
            if isinstance(detail, dict):
                print("ERROR CODE:", detail.get("code"), "TYPE:", detail.get("type"), flush=True)
    report = usage_report(runtime.trace.directory / "trace.jsonl", model.model, model.provider)
    report["run_status"] = runtime.state["status"]
    runtime.trace.save("cost_estimate.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 1 if failed or runtime.state["status"] != "completed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
