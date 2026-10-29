"""Phase 1 runtime: real tool loops and message routing, no policy assurance.

Run only against disposable copies. cwd is not an OS sandbox. The prompt
policies intentionally are not command/upload/message enforcement rules here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

from .anonymizer import LocalAnonymizer, SUPPORTED_SUFFIXES
from .tools import agent_tools, run_command
from .tool_protocol import ToolProtocolError, arguments_for, normalize_message


ROLES = ("analyzer", "planner", "executor")
GENERATED = {".organizer", ".anonymized"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            value.update(chunk)
    return value.hexdigest()


def inventory(root: Path) -> list[dict]:
    root = root.resolve(strict=True)
    result = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in GENERATED for part in rel.parts) or path.is_symlink() or not path.is_file():
            continue
        # Avoid traversing files beneath symlinked directories as well.
        if any(parent.is_symlink() for parent in path.parents
               if parent != root and parent.is_relative_to(root)):
            continue
        result.append({"file_id": f"file_{len(result) + 1:03d}",
                       "path": rel.as_posix(), "size": path.stat().st_size,
                       "supported": path.suffix.lower() in SUPPORTED_SUFFIXES})
    return result


class Trace:
    def __init__(self, directory: Path, run_id: str):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        directory.chmod(0o700)
        self.run_id = run_id
        self.sequence = 0

    def event(self, kind: str, **fields) -> dict:
        self.sequence += 1
        event = {"event_id": f"event_{self.sequence:05d}", "run_id": self.run_id,
                 "timestamp": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        with (self.directory / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def save(self, name: str, data) -> None:
        target = self.directory / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)


class Runtime:
    def __init__(self, workspace: str | Path, model, *, apply: bool = False,
                 confirm=None, max_calls: int = 100, max_steps: int = 64,
                 known_names=(), progress=None, confirmation_source=None,
                 executor_batch_size: int | None = None):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory.")
        self.model = model
        if executor_batch_size is not None and (not isinstance(executor_batch_size, int)
                                                or isinstance(executor_batch_size, bool)
                                                or executor_batch_size < 1):
            raise ValueError("executor_batch_size must be a positive integer or None.")
        self.executor_batch_size = executor_batch_size
        self.apply = apply
        self.confirm = confirm or (lambda plan: False)
        self.confirmation_source = confirmation_source or (
            "interactive_user" if model.is_real_model else "scripted_test_harness")
        self.progress = progress or (lambda text: None)
        self.max_calls, self.max_steps = max_calls, max_steps
        self.calls = 0
        self.run_id = "run_" + uuid.uuid4().hex[:16]
        self.trace = Trace(self.workspace / ".organizer" / "runs" / self.run_id, self.run_id)
        self.anonymizer = LocalAnonymizer(self.workspace, known_names)
        self.queue = deque()
        self.plans = []
        self.moves = []
        self.histories = {}
        prompts = Path(__file__).parent / "prompts"
        for role in ROLES:
            instructions = (prompts / "common.md").read_text() + "\n" + (prompts / f"{role}.md").read_text()
            self.histories[role] = [{"role": "system", "content": instructions}]
        self.trace.event("run_started", phase=1, policy_enforcement=False,
                         backend=model.model, real_llm=model.is_real_model,
                         provider=getattr(model, "provider", "unspecified" if model.is_real_model else "scripted"),
                         max_rpm=getattr(model, "max_rpm", None),
                         executor_batch_size=executor_batch_size,
                         communication_flow="executor_evidence_via_planner",
                         role_permissions="prompt_only", shared_toolset=True,
                         apply=apply, max_calls=max_calls,
                         note="Full baseline trace; use synthetic data. Not a sandbox.")
        self.state = {"run_id": self.run_id, "workspace": str(self.workspace),
                      "phase": 1, "real_llm": model.is_real_model,
                      "model": model.model, "status": "running", "moves": self.moves,
                      "provider": getattr(model, "provider", "unspecified" if model.is_real_model else "scripted"),
                      "max_rpm": getattr(model, "max_rpm", None),
                      "executor_batch_size": executor_batch_size,
                      "communication_flow": "executor_evidence_via_planner",
                      "role_permissions": "prompt_only", "shared_toolset": True,
                      "plans": self.plans, "model_calls": 0}
        self._save()

    def _save(self):
        self.state["model_calls"] = self.calls
        self.trace.save("manifest.json", self.state)

    def send(self, sender: str, recipient: str, type: str, payload: dict) -> dict:
        # Envelope structure is plumbing. No Phase 2 role/type/privacy policies
        # are enforced against the contents here.
        if recipient not in (*ROLES, "runtime") or not isinstance(payload, dict):
            raise ValueError("Invalid message recipient or payload shape.")
        message = {"run_id": self.run_id, "message_id": "msg_" + uuid.uuid4().hex[:12],
                   "sender": sender, "recipient": recipient, "type": type, "payload": payload}
        self.trace.event("agent_message", message=message)
        if recipient != "runtime":
            self.queue.append((recipient, message))
        return {"message_id": message["message_id"], "queued": recipient != "runtime"}

    def submit_plan(self, summary: str, operations: list) -> dict:
        if not isinstance(summary, str) or not isinstance(operations, list):
            raise ValueError("Invalid plan structure.")
        if len(self.plans) >= 4:
            raise ValueError("Replanning budget reached; stop and report remaining work.")
        registered = []
        invalid_sources = []
        current_files = {row["path"] for row in inventory(self.workspace)}
        for i, operation in enumerate(operations, 1):
            if not isinstance(operation, dict) or not all(
                isinstance(operation.get(k), str) for k in ("source", "destination", "reason")
            ):
                raise ValueError("Each operation needs source, destination and reason strings.")
            op = dict(operation, operation_id=f"op_{i:03d}")
            source = self.workspace / op["source"]
            if op["source"] not in current_files:
                invalid_sources.append(op["source"])
            op["source_sha256"] = digest(source) if source.is_file() else None
            registered.append(op)
        if invalid_sources:
            # Ordinary proposal validation: don't register a typo as an
            # executable plan. This does not constrain command permissions.
            result = {"error": "invalid_source_paths", "invalid_sources": invalid_sources,
                      "message": "Use existing source paths with exact capitalization, spaces and underscores. "
                                 "Ask Executor for a fresh inventory if needed; correct and resubmit. "
                                 "Runtime does not provide directory discovery to Planner."}
            self.trace.event("plan_validation_failed", result=result)
            return result
        content = {"summary": summary, "operations": registered}
        plan = {**content, "plan_id": "plan_" + uuid.uuid4().hex[:12],
                "version": len(self.plans) + 1,
                "digest": hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(),
                "confirmed": False}
        self.plans.append(plan)
        self.trace.event("plan_proposed", plan=plan)
        self._save()
        self.progress(f"Plan v{plan['version']}: {len(registered)} operation(s).")
        if self.apply and registered:
            decision = self.confirm(plan)
            feedback = decision.strip() if isinstance(decision, str) else ""
            accepted = decision is True
            plan["confirmed"] = accepted
            self.trace.event("confirmation", plan_id=plan["plan_id"], version=plan["version"],
                             digest=plan["digest"], accepted=accepted,
                             source=self.confirmation_source)
            if not accepted and feedback:
                self.trace.event("plan_review_feedback", plan_id=plan["plan_id"], feedback=feedback,
                                 source=self.confirmation_source)
                return {"plan_id": plan["plan_id"], "version": plan["version"],
                        "confirmed": False, "review_feedback": feedback,
                        "instruction": "Revise the proposal using this operator feedback and submit again. "
                                       "The revised version requires separate confirmation."}
        self._save()
        return {"plan_id": plan["plan_id"], "version": plan["version"],
                "digest": plan["digest"], "confirmed": plan["confirmed"],
                "preview_only": not self.apply,
                "pending_operation_ids": self._pending_operations(plan, batch=True),
                "all_pending_operation_ids": self._pending_operations(plan)}

    def _pending_operations(self, plan: dict, *, batch: bool = False) -> list[str]:
        pending = []
        for op in plan["operations"]:
            source, target = [self.workspace / op[key] for key in ("source", "destination")]
            complete = (not source.exists() and target.is_file()
                        and digest(target) == op["source_sha256"])
            if not complete:
                pending.append(op["operation_id"])
        return pending[:self.executor_batch_size] if batch and self.executor_batch_size else pending

    def get_plan(self, plan_id: str, version: int) -> dict:
        plan = next((p for p in self.plans if p["plan_id"] == plan_id and p["version"] == version), None)
        if plan is None:
            return {"error": "unknown_plan", "message": "No matching registered plan/version."}
        current = self.plans[-1] is plan
        return {"plan": plan, "current": current, "execution_enabled": self.apply,
                "confirmed": plan["confirmed"],
                "pending_operation_ids": self._pending_operations(plan, batch=True),
                "all_pending_operation_ids": self._pending_operations(plan)}

    def _move_intent(self, argv):
        # Only simple, single-file mv invocations are journaled for undo.
        # Unsupported commands still execute in this baseline; do not imply
        # that recording a known form enforces a command allowlist.
        if not isinstance(argv, list) or not argv or Path(argv[0]).name != "mv":
            return None
        args = [v for v in argv[1:] if v not in ("--", "-n")]
        if len(args) != 2 or any(v.startswith("-") for v in args):
            return None
        source, destination = [self.workspace / value for value in args]
        source, destination = source.resolve(), destination.resolve()
        if not all(p.is_relative_to(self.workspace) for p in (source, destination)):
            return None
        if not source.is_file() or destination.exists():
            return None
        return {"source": source.relative_to(self.workspace).as_posix(),
                "destination": destination.relative_to(self.workspace).as_posix(),
                "sha256": digest(source)}

    def tool(self, actor: str, name: str, arguments: dict) -> dict:
        names = {s["function"]["name"] for s in agent_tools(actor)}
        if name not in names:
            raise ValueError("Tool is not registered for this agent.")
        if name == "send_message":
            return self.send(actor, **arguments)
        if name == "submit_plan":
            return self.submit_plan(**arguments)
        if name == "list_files":
            return {"inventory": inventory(self.workspace)}
        if name == "get_plan":
            return self.get_plan(**arguments)
        if name == "anonymize_file":
            return self.anonymizer.anonymize_file(**arguments)
        if name == "upload_file":
            # This selects file bytes as a tool result for the NEXT model call.
            # No /files endpoint is used. Critically, Phase 1 does not call
            # resolve_output here: original files remain selectable.
            path = self.workspace / arguments["file"]
            if path.stat().st_size > 2_000_000:
                raise ValueError("Selected file exceeds 2 MB.")
            content = path.read_text(encoding="utf-8")
            if len(content) > 16000:
                raise ValueError("Selected text exceeds 16,000 characters; request a smaller input.")
            return {"selected_file": arguments["file"], "content": content,
                    "transfer": "next_model_request", "provenance_enforced": False}
        if name == "run_command":
            intent = self._move_intent(arguments.get("argv"))
            if intent:
                self.trace.event("move_intent", actor=actor, **intent)
            result = run_command(self.workspace, **arguments)
            if intent:
                source, destination = [self.workspace / intent[k] for k in ("source", "destination")]
                if not source.exists() and destination.is_file() and digest(destination) == intent["sha256"]:
                    self.moves.append({**intent, "undone": False})
                    self.trace.event("move_completed", actor=actor, **intent)
                    self._save()
            return result
        raise ValueError("Unknown tool.")

    def activate(self, role: str, message: dict):
        history = self.histories[role]
        history.append({"role": "user", "content": json.dumps(message, ensure_ascii=False)})
        tools = agent_tools(role)
        self.progress(f"{role}: {message['type']}")
        handed_off = False
        reminded = False
        last_protocol_error, repeated_protocol_errors = None, 0
        for _ in range(self.max_steps):
            if self.calls >= self.max_calls:
                raise RuntimeError("Global model-call budget reached.")
            self.calls += 1
            self.trace.event("model_request", agent=role, messages=history, tools=tools)
            response = self.model.complete(role, history, tools)
            self.trace.event("model_response", agent=role, **response)
            if response["message"].get("role") != "assistant":
                raise RuntimeError("Model returned a non-assistant message.")
            assistant, repairs, fallback_error = normalize_message(response["message"], tools)
            if repairs:
                self.trace.event("tool_call_compatibility", agent=role, repairs=repairs,
                                 effective_message=assistant,
                                 note="Transport normalization only; original output is in model_response.")
            history.append(assistant)
            if assistant.get("refusal"):
                return
            requests = assistant.get("tool_calls", [])
            if not requests:
                if (not handed_off or fallback_error) and not reminded and not assistant.get("refusal"):
                    instruction = (
                        "Runtime reminder: your last text answer did not submit or deliver anything. "
                        "Prefer a native tool call according to your role policies. If unavailable, emit exactly "
                        'one JSON object: {"tool":"TOOL_NAME","arguments":{...}}. '
                        "Do not add prose or special tool markers, invent arguments/evidence/approval, "
                        "or repeat an unchanged invalid call. If unable to proceed, say so.")
                    if fallback_error:
                        instruction += " Protocol feedback: " + json.dumps(fallback_error)
                    self.trace.event("agent_reminder", agent=role, instruction=instruction,
                                     protocol_error=fallback_error,
                                     reason="invalid_json_tool_fallback" if fallback_error
                                            else "activation_ended_without_handoff")
                    history.append({"role": "user", "content": instruction})
                    self.progress(f"{role}: reminder to use the handoff/submission tool")
                    reminded = True
                    continue
                return
            for request in requests:
                function = request["function"]
                self.trace.event("tool_request", agent=role, call_id=request["id"], **function)
                protocol_error = None
                try:
                    arguments, _ = arguments_for(function["name"], function["arguments"], tools)
                    result = self.tool(role, function["name"], arguments)
                except ToolProtocolError as error:
                    # Schema/transport feedback, never a role or workflow gate.
                    result = error.feedback()
                    protocol_error = (function["name"], error.code, error.path, tuple(error.missing or ()))
                except (ValueError, TypeError, OSError, UnicodeError, subprocess.SubprocessError) as error:
                    # Do not log exception text: it can contain file content.
                    result = {"error": type(error).__name__,
                              "message": "Tool failed; check arguments and report or retry."}
                event = self.trace.event("tool_result", agent=role, call_id=request["id"],
                                         tool=function["name"], result=result)
                result = {**result, "tool_record_id": event["event_id"]}
                if function["name"] in {"send_message", "submit_plan"} and not result.get("error"):
                    handed_off = True
                history.append({"role": "tool", "tool_call_id": request["id"],
                                "content": json.dumps(result, ensure_ascii=False)})
                repeated_protocol_errors = (repeated_protocol_errors + 1
                                            if protocol_error and protocol_error == last_protocol_error
                                            else int(protocol_error is not None))
                last_protocol_error = protocol_error
                if repeated_protocol_errors >= 3:
                    self.trace.event("tool_protocol_stopped", agent=role, error=result["error"],
                                     note="Three consecutive identical protocol errors; no further retries.")
                    raise RuntimeError(f"Repeated unusable tool calls: {role}.")
        raise RuntimeError(f"Agent step budget reached: {role}.")

    def _reused_analysis(self, source_directory: Path, before: list[dict]) -> dict:
        """Reuse a recorded report only if every source still matches its hash.

        This is explicit recovery, not a new Analyzer model response. Evidence
        IDs are qualified by their original run, with the source trace digest.
        """
        source_trace = source_directory / "trace.jsonl"
        source_state = json.loads((source_directory / "manifest.json").read_text())
        events = [json.loads(line) for line in source_trace.read_text().splitlines()]
        reports = [event["message"] for event in events if event["kind"] == "agent_message"
                   and event["message"]["sender"] == "analyzer"
                   and event["message"]["recipient"] == "planner"
                   and event["message"]["type"] == "analysis_report"]
        if not reports or source_state["before"] != before:
            raise ValueError("Recorded analysis does not match the current inventory.")
        payload = reports[-1]["payload"]
        files = payload.get("files", [])
        if (len(files) != len(before)
                or {row["path"] for row in files} != {row["path"] for row in before}):
            raise ValueError("Recorded analysis lacks matching hashes for every current file.")
        # Compare against actual recorded shasum output, not a model's copied
        # hash string. Explicitly record transcription corrections in recovery.
        hash_commands = {event["call_id"] for event in events
                         if event["kind"] == "tool_request" and event["agent"] in {"analyzer", "executor"}
                         and event["name"] == "run_command"
                         and json.loads(event["arguments"]).get("argv", [])[:3] == ["shasum", "-a", "256"]}
        recorded_hashes = {}
        for event in events:
            if (event["kind"] == "tool_result" and event["call_id"] in hash_commands
                    and event["result"].get("exit_code") == 0):
                for line in event["result"].get("stdout", "").splitlines():
                    match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
                    if match:
                        recorded_hashes[match[2]] = match[1]
        corrections = []
        for row in files:
            actual = digest(self.workspace / row["path"])
            if actual != recorded_hashes.get(row["path"]):
                raise ValueError("Current file differs from recorded tool hash evidence.")
            if row.get("sha256") != actual:
                corrections.append({"path": row["path"], "reported_sha256": row.get("sha256"),
                                    "tool_verified_sha256": actual})
                row["sha256"] = actual
        source_run_id = source_state["run_id"]
        for row in files:
            row["evidence_refs"] = [f"{source_run_id}:{ref}" for ref in row.get("evidence_refs", [])]
        provenance = {"source_run_id": source_run_id, "source_trace": str(source_trace.resolve()),
                      "source_trace_sha256": digest(source_trace),
                      "inventory_and_all_source_hashes_verified": True,
                      "hash_transcription_corrections": corrections,
                      "new_analyzer_model_calls": 0}
        payload["analysis_provenance"] = provenance
        self.state["analysis_provenance"] = provenance
        self.trace.event("analysis_reused", **provenance)
        return payload

    def run(self, task: str, *, analysis_from: Path | None = None) -> dict:
        try:
            before = inventory(self.workspace)
            self.state["before"] = before
            before_hashes = Counter(digest(self.workspace / row["path"]) for row in before)
            if analysis_from is None:
                self.send("runtime", "analyzer", "organize_request", {
                    "task": task,
                    "content_permission": "User requests classification using local anonymized content.",
                    "execution_enabled": self.apply})
            else:
                payload = self._reused_analysis(Path(analysis_from), before)
                payload["task"] = task
                self.send("analyzer", "planner", "analysis_report", payload)
            # Agents choose their own handoffs/continuations. Drain only real
            # delivered messages; never manufacture a route to finish a plan.
            while self.queue:
                role, message = self.queue.popleft()
                self.activate(role, message)
            after = inventory(self.workspace)
            self.state["after"] = after
            preserved = before_hashes == Counter(digest(self.workspace / row["path"]) for row in after)
            self.state["contents_preserved"] = preserved
            confirmed = [p for p in self.plans if p["confirmed"]]
            if confirmed:
                latest = confirmed[-1]
                complete = all(
                    not (self.workspace / op["source"]).exists()
                    and (self.workspace / op["destination"]).is_file()
                    and digest(self.workspace / op["destination"]) == op["source_sha256"]
                    for op in latest["operations"])
                status = "completed" if complete and preserved else "incomplete"
            elif not self.plans:
                status = "no_plan"
            else:
                status = "preview_ready" if not self.apply else (
                    "no_changes" if not self.plans[-1]["operations"] else "not_executed")
            self.state["status"] = status
            self.trace.event("run_finished", status=status, contents_preserved=preserved,
                             model_calls=self.calls, completed_moves=len(self.moves))
            self._save()
            return self.state
        except BaseException as error:
            self.state["status"] = "failed"
            self.trace.event("run_failed", error_type=type(error).__name__)
            self._save()
            raise


def undo_run(run_directory: str | Path, confirm) -> dict:
    """Undo recognized moves without overwriting; keep generated artifacts/logs.

    This is a local recovery feature, not a Phase 2 policy evaluator.
    """
    directory = Path(run_directory).resolve(strict=True)
    manifest = json.loads((directory / "manifest.json").read_text())
    root = Path(manifest["workspace"]).resolve(strict=True)
    pending = [m for m in reversed(manifest["moves"]) if not m.get("undone")]
    if not confirm(pending):
        return {"status": "cancelled", "restored": 0, "conflicts": []}
    restored, conflicts = 0, []
    for move in pending:
        original, current = [(root / move[key]).resolve() for key in ("source", "destination")]
        if (not all(p.is_relative_to(root) for p in (original, current))
                or not current.is_file() or original.exists()
                or digest(current) != move["sha256"]):
            conflicts.append(move["destination"])
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        # link fails if the destination already exists; same filesystem only.
        os.link(current, original)
        current.unlink()
        move["undone"] = True
        restored += 1
        temporary = directory / "manifest.undo.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(directory / "manifest.json")
    result = {"status": "undone" if not conflicts else "partial_undo",
              "restored": restored, "conflicts": conflicts}
    with (directory / "trace.jsonl").open("a") as stream:
        stream.write(json.dumps({"kind": "undo_result", "event_id": "undo_" + uuid.uuid4().hex[:12],
                                 "run_id": manifest["run_id"],
                                 "timestamp": datetime.now(timezone.utc).isoformat(), **result}) + "\n")
    return result
