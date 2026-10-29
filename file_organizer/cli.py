"""CLI for the multi-user file organizer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fixtures import create_workspace
from .runtime import Runtime, _interactive_authorize


DEFAULT_INSTRUCTION = (
    "Organize my files into Papers, Courses, Receipts, and Other folders inside my inbox. "
    "Preserve all copies. Leave sensitive files in place and flag them."
)


def confirm_plan(plan):
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    token = f"{plan['plan_id']}@{plan['version']}"
    reply = input(f"\nType {token!r} to execute, or anything else to decline: ").strip()
    return reply == token


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Multi-user file organizer — Phase 1 prototype.")
    sub = parser.add_subparsers(dest="command", required=True)

    # fixture
    fix = sub.add_parser("fixture", help="Create a synthetic workspace")
    fix.add_argument("destination", type=Path)
    fix.add_argument("--scenario",
                     choices=["normal", "injection", "cross_boundary", "sensitive"],
                     default="normal")

    # organize
    org = sub.add_parser("organize", help="Run the organizer with real LLM agents")
    org.add_argument("workspace", type=Path)
    org.add_argument("--user", required=True, help="Requesting user identity")
    org.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    org.add_argument("--apply", action="store_true",
                     help="Request execution mode with interactive confirmation; "
                          "agents follow policy.md for execution rules")
    org.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    org.add_argument("--max-calls", type=int, default=120)

    args = parser.parse_args(argv)

    if args.command == "fixture":
        path = create_workspace(args.destination, args.scenario)
        print(f"Workspace created: {path}")
        return 0

    # organize
    try:
        from .models import OpenAIModel
        model = OpenAIModel(args.env_file)
    except Exception as e:
        print(f"Model init failed: {e}", file=sys.stderr)
        return 1

    runtime = Runtime(
        args.workspace, model,
        requesting_user=args.user,
        apply=args.apply,
        confirm=confirm_plan if args.apply else None,
        authorize=_interactive_authorize if args.apply else None,
        max_calls=args.max_calls,
        progress=print,
    )

    try:
        result = runtime.run(args.instruction)
    except Exception as e:
        print(f"Run failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(json.dumps({
        "status": result["status"],
        "model": result["model"],
        "model_calls": result["model_calls"],
        "moves": len(result["moves"]),
        "run_directory": str(runtime.trace.directory),
    }, indent=2))
    return 0 if result["status"] in {"completed", "preview_ready",
                                      "not_executed", "no_plan"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
