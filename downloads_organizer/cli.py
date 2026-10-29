"""CLI for Phase 1, fixtures, explicit scripted smoke tests and recovery."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .demo import ScriptedDemoModel, create_fixture
from .runtime import Runtime, undo_run


DEFAULT_TASK = (
    "Classify these synthetic/downloaded files using locally anonymized content. "
    "Organize papers, course materials and receipts; preserve original names and "
    "all copies; keep duplicates in Duplicates. Leave unsupported files in place."
)


def confirm_plan(plan):
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    token = f"{plan['plan_id']}@{plan['version']}"
    reply = input(f"Type {token} to execute, revise: <feedback> to request changes, "
                  "or anything else to decline: ").strip()
    if reply.startswith("revise:"):
        return reply.removeprefix("revise:").strip() or False
    return reply == token


def main(argv=None):
    parser = argparse.ArgumentParser(description=(
        "Phase 1 file organizer. Prompt-only policies; commands run as your OS user. "
        "Use disposable synthetic workspaces, not your real Downloads yet."))
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="Open the local, read-only agent trace viewer")
    view.add_argument("--run", type=Path, action="append", default=[], help="Run directory or JSONL; repeat for linked runs")
    view.add_argument("--port", type=int, default=8765)
    view.add_argument("--no-browser", action="store_true")
    organize = commands.add_parser("organize", help="Use three real agents with the configured model provider")
    organize.add_argument("workspace", type=Path)
    organize.add_argument("--task", default=DEFAULT_TASK)
    organize.add_argument("--apply", action="store_true", help="Ask confirmation and enable Executor")
    organize.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    organize.add_argument("--max-calls", type=int, default=100)
    organize.add_argument("--max-steps", type=int, default=64, help="Tool-loop steps per agent activation")
    organize.add_argument("--executor-batch-size", type=int,
                          help="Limit pending operations per Executor activation; keep one confirmed full plan")
    organize.add_argument("--names-file", type=Path, help="Local-only known names, one per line")
    fixture = commands.add_parser("fixture", help="Create disposable synthetic files")
    fixture.add_argument("destination", type=Path)
    fixture.add_argument("--scenario", choices=["normal", "conflict", "injection", "hard", "deletion_clean", "deletion_attack"], default="normal")
    demo = commands.add_parser("demo", help="Offline scripted smoke test; not a real LLM trace")
    demo.add_argument("--scenario", choices=["normal", "conflict", "injection"], default="normal")
    demo.add_argument("--output", type=Path)
    undo = commands.add_parser("undo", help="Preview and confirm recovery of recorded moves")
    undo.add_argument("run_directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "view":
            from .viewer import main as viewer_main
            viewer_args = ["--port", str(args.port)]
            for path in args.run:
                viewer_args.extend(["--run", str(path)])
            if args.no_browser:
                viewer_args.append("--no-browser")
            return viewer_main(viewer_args)
        if args.command == "fixture":
            print(create_fixture(args.destination, args.scenario))
            return 0
        if args.command == "undo":
            def confirm(moves):
                print(json.dumps(moves, ensure_ascii=False, indent=2))
                return input("Type UNDO to restore these moves: ").strip() == "UNDO"
            result = undo_run(args.run_directory, confirm)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] != "partial_undo" else 2
        if args.command == "demo":
            destination = args.output or Path("demo/workspaces") / (args.scenario + "-" + uuid.uuid4().hex[:8])
            workspace = create_fixture(destination, args.scenario)
            model, apply, confirmation = ScriptedDemoModel(), True, lambda plan: True
            task, names, max_calls = DEFAULT_TASK, [], 160
            max_steps = 100
            print("SCRIPTED SMOKE TEST — no LLM calls, not a course submission trace.")
        else:
            from .models import OpenAIModel
            model = OpenAIModel(args.env_file)
            workspace, apply, confirmation = args.workspace, args.apply, confirm_plan
            task, max_calls = args.task, args.max_calls
            max_steps = args.max_steps
            names = args.names_file.read_text(encoding="utf-8").splitlines() if args.names_file else []
        runtime = Runtime(workspace, model, apply=apply, confirm=confirmation,
                          known_names=names, max_calls=max_calls, max_steps=max_steps,
                          executor_batch_size=getattr(args, "executor_batch_size", None),
                          progress=print)
        result = runtime.run(task)
        if not apply and result["plans"]:
            print("Proposed plan (preview only):")
            print(json.dumps(result["plans"][-1], ensure_ascii=False, indent=2))
        print(json.dumps({"status": result["status"], "model": result["model"],
                          "real_llm": result["real_llm"], "model_calls": result["model_calls"],
                          "moves": len(result["moves"]), "run_directory": str(runtime.trace.directory)}, indent=2))
        return 0 if result["status"] in {"completed", "preview_ready", "not_executed", "no_changes"} else 2
    except KeyboardInterrupt:
        print("Stopped; inspect the run manifest before retrying.", file=sys.stderr)
        return 130
    except Exception as error:
        # Avoid dumping provider exceptions that could expose request payloads.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(f"Run failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
